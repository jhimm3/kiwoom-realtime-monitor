"""One mock account's client, execution owner and connection lifetime."""

from __future__ import annotations

import asyncio
import hmac
import inspect
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Callable

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
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository

from .execution_runtime import ExecutionRuntime, ManualMockOrderGateway
from .market_observations import KST, as_kst
from .mock_account_monitor import MockAccountMonitor, MockAccountRealtimeCollector
from .observation_frame_recovery import drain_thread_call
from .rest_broker import CentralRestBroker, MOCK_ACCOUNT_ENDPOINTS
from .credential_runtime import CredentialRuntimeHooks, CredentialOperationError, ValidatedCredential
from .account_query import AccountQuerySessionManager


MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION = "mock_account_runtime_context/v1"


class MockAccountRuntimeMode(StrEnum):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"


@dataclass(frozen=True)
class MockAutomationRuntimeContext:
    version: str
    account_ref: str
    execution_run_id: str
    spec_id: str
    admission_id: str
    control_revision: int
    credential_revision: int

    def __post_init__(self) -> None:
        if self.version != MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION:
            raise ValueError("unsupported mock account runtime context")
        if not all(str(getattr(self, name)).strip() for name in (
            "account_ref", "execution_run_id", "spec_id", "admission_id",
        )):
            raise ValueError("automatic runtime context identity is required")
        if not self.execution_run_id.startswith("mock_auto_run_"):
            raise ValueError("automatic execution run id is invalid")
        if self.control_revision <= 0 or self.credential_revision <= 0:
            raise ValueError("automatic runtime revisions must be positive")


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
                 legacy_bundle: Any = None, legacy_monitor_enabled: bool = False, on_change: Any = None,
                 now_provider: Callable[[], datetime] | None = None, capture_scope=None):
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient
        self.store, self.vault, self.hmac_key = store, vault, hmac_key
        self._now_provider = now_provider
        self._capture_scope = capture_scope
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
        self._position_order_observation_account = None
        self._close_task = None
        self._on_change = on_change or (lambda *_: None)

    async def _publish_change(self, profile_id, bundle):
        # Withdrawal must finish downstream runner work before the old bundle
        # releases its native lease. Existing synchronous listeners still work.
        changed = self._on_change(profile_id, bundle)
        if inspect.isawaitable(changed):
            await changed

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

    def position_shadow_observation(self, account_ref):
        """Use an admitted, verified mock owner; never boot or query an account."""
        if self._closing:
            return None
        bundles = [b for b in self._bundles.values() if b.binding is not None
                   and b.binding.scope.account_ref == account_ref
                   and b.binding.scope.environment.value == "mock" and not b._closing]
        if len(bundles) != 1:
            return None
        return bundles[0].monitor.latest_position_observation

    def set_position_order_observation_account(self, account_ref):
        self._position_order_observation_account = account_ref
        for bundle in self._bundles.values():
            bundle.set_position_order_observation_account(account_ref)

    def has_started_monitor(self) -> bool:
        """Published profiles with a started monitor; not permission to place orders."""
        return any(bundle.monitor.is_started for bundle in self._bundles.values())

    def active_revision(self, profile_id: str):
        return self._revisions.get(profile_id)

    def issue_selected_order_identity_proof(self, packet, digest, *, credential_profile_id, broker_evidence=None):
        """Sign only this owner's stopped native source facts; never expose its key."""
        from kiwoom_monitor.application.selected_order_recovery import issue_source_identity_proof
        from kiwoom_monitor.domain.selected_order_evidence import encode_selected_order_evidence
        if encode_selected_order_evidence(packet)[1] != digest:
            raise ValueError('selected source packet digest changed')
        facts = self.store.load_selected_order_identity_context(account_ref=packet['account_ref'],
            credential_profile_id=credential_profile_id, source_packet=packet)
        return issue_source_identity_proof(packet, digest, binding=facts['binding'],
            identity_fingerprint=facts['identity_fingerprint'], identity_key=self.hmac_key, broker_evidence=broker_evidence)

    async def verify_selected_order_identity(self, packet, digest, source_proof, *, credential_profile_id,
                                             credential_revision, account_ref, binding_revision, broker_evidence_provider=None,
                                             on_verified=None):
        """Use the current credential owner and existing broker for one explicit identity read."""
        from kiwoom_monitor.application.selected_order_recovery import verify_source_identity_proof, broker_evidence_from_dict
        from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import KiwoomAccountIdentityReader
        from kiwoom_monitor.domain.order_contract import AccountEnvironment
        lock = self._account_locks.setdefault(account_ref, asyncio.Lock())
        async with lock:
            bundle = self.bundle(credential_profile_id)
            def current():
                return (not self._closing and credential_profile_id not in self._pending_profiles
                    and self.bundle(credential_profile_id) is bundle and bundle is not None
                    and getattr(bundle, 'canonical_account_ref', bundle.account_ref) == account_ref
                    and self.active_revision(credential_profile_id) == credential_revision)
            if not current():
                raise RuntimeError('SELECTED_RECOVERY_TARGET_CREDENTIAL_CHANGED')
            facts = await drain_thread_call(self.store.load_selected_order_identity_context, account_ref=account_ref,
                credential_profile_id=credential_profile_id, binding_revision=binding_revision)
            identity = await KiwoomAccountIdentityReader(bundle.broker, environment=AccountEnvironment.MOCK,
                hmac_key=self.hmac_key, now_provider=bundle.runtime.server_now).verify()
            if not current() or identity.identity_fingerprint != facts['identity_fingerprint']:
                raise RuntimeError('SELECTED_RECOVERY_TARGET_IDENTITY_CHANGED')
            matched = verify_source_identity_proof(packet, digest, source_proof, identity_key=self.hmac_key,
                target_identity=identity, target_binding=facts['binding'], credential_revision=credential_revision)
            evidence = (await broker_evidence_provider(bundle=bundle, identity=matched, packet=packet,
                source_evidence=broker_evidence_from_dict(source_proof.get('broker_evidence')))
                if broker_evidence_provider is not None and matched.status == 'MATCH' else None)
            latest = await drain_thread_call(self.store.load_selected_order_identity_context, account_ref=account_ref,
                credential_profile_id=credential_profile_id, binding_revision=binding_revision)
            if not current() or latest != facts:
                raise RuntimeError('SELECTED_RECOVERY_TARGET_BINDING_CHANGED')
            if on_verified is not None:
                return await on_verified(matched, evidence, current)
            return matched, evidence

    async def install_verified_selected_order(self, packet, *, identity, source_proof, broker_evidence, current_owner):
        """Drain the existing target owner before installing historical management data.

        The caller already owns verify_selected_order_identity's account lock.
        Its current runtime token proves only the live target lease, never an
        imported source permission. Successful installation keeps entries off.
        """
        profile = identity.credential_profile_id
        bundle = self.bundle(profile)
        if bundle is None or not current_owner() or bundle.account_ref != identity.target_account_ref:
            raise RuntimeError('SELECTED_BOOTSTRAP_TARGET_EPOCH_CHANGED')
        runtime, gateway = bundle.runtime, getattr(bundle, 'gateway', None)
        native_runtime = isinstance(runtime, ExecutionRuntime) and runtime._active
        previous_enabled = runtime._new_orders_enabled if native_runtime else None
        current_target_runtime = None
        drained = False
        if native_runtime:
            if gateway is not None:
                await gateway.begin_credential_change()
                drained = True
            runtime.set_new_orders_enabled(False)
            current_target_runtime = dict(account_ref=runtime.account_ref, run_id=runtime.run_id,
                                          owner_token=runtime.owner_token)
        try:
            if not current_owner():
                raise RuntimeError('SELECTED_BOOTSTRAP_TARGET_EPOCH_CHANGED')
            return await drain_thread_call(self.store.install_selected_order_management, packet,
                identity=identity, source_proof=source_proof, broker_evidence=broker_evidence,
                current_owner=current_owner,
                **({'current_target_runtime': current_target_runtime} if current_target_runtime is not None else {}))
        except BaseException:
            # A lost COMMIT response must not reopen the manual gateway beside
            # an installed original order. Its immutable receipt is the oracle.
            from .selected_order_bootstrap import INSTALLATIONS
            row = await drain_thread_call(self.store.load_document, INSTALLATIONS,
                packet['account_ref'], packet['order']['intent_id'])
            receipt = row['document'] if row is not None else None
            if (receipt is not None and (receipt.get('operation_id'), receipt.get('packet_sha256'),
                    receipt.get('target_account_ref'), receipt.get('credential_profile_id'),
                    receipt.get('credential_revision'), receipt.get('binding_revision'))
                    == (packet['operation_id'], identity.packet_sha256, identity.target_account_ref,
                        profile, identity.credential_revision, identity.binding_revision)):
                return {'receipt': receipt, 'replayed': True}
            if receipt is None and native_runtime and current_owner():
                latest = await drain_thread_call(self.store.load_latest_account_binding, profile, 'kiwoom', 'mock')
                if (latest is not None and (latest['account_ref'], latest['binding_revision'], latest['verification_method'])
                        == (identity.target_account_ref, identity.binding_revision, 'ka00001') and current_owner()):
                    runtime.set_new_orders_enabled(previous_enabled)
                    if drained:
                        gateway.end_credential_change()
            raise

    async def prepare_selected_order_management(self, operation_id, *, credential_profile_id,
            credential_revision, account_ref, binding_revision, packet_sha256, expected_settings_revision,
            broker_evidence_provider=None):
        """Prepare one original reconciliation owner from the installed closure only."""
        from kiwoom_monitor.domain.selected_order_evidence import COLLECTION, OWNER, selected_order_management_context_from_dict
        from .selected_order_bootstrap import INSTALLATIONS, ASSESSMENTS, CURRENT_MANAGEMENT
        preserved = await drain_thread_call(self.store.load_document, COLLECTION, OWNER, operation_id)
        if preserved is None or preserved['document']['receipt']['sha256'] != packet_sha256:
            raise RuntimeError('SELECTED_MANAGEMENT_INSTALLED_PACKET_CHANGED')
        packet = preserved['document']['packet']
        installed = await drain_thread_call(self.store.load_document, INSTALLATIONS,
            packet['account_ref'], packet['order']['intent_id'])
        if installed is None:
            raise RuntimeError('SELECTED_MANAGEMENT_INSTALLATION_REQUIRED')
        assessment = await drain_thread_call(self.store.load_document, ASSESSMENTS, OWNER,
            installed['document']['assessment_id'])
        if assessment is None:
            raise RuntimeError('SELECTED_MANAGEMENT_SOURCE_PROOF_MISSING')
        source_proof = assessment['document']['source_identity_proof']

        async def prepare(identity, evidence, current_owner):
            old = self.bundle(credential_profile_id)
            settings = await drain_thread_call(self.store.load_account_settings,
                dict(broker='kiwoom', environment='mock', account_ref=account_ref))
            if (settings['revision'] != expected_settings_revision
                    or self._settings_revisions.get(credential_profile_id) != expected_settings_revision
                    or settings['active_profile_id'] != credential_profile_id or not settings['monitor_enabled']):
                raise RuntimeError('ACCOUNT_SETTINGS_REVISION_CONFLICT')
            verified_current = current_owner
            def current_owner():
                return (verified_current()
                    and self._settings_revisions.get(credential_profile_id) == expected_settings_revision)
            previous_context = getattr(old, 'selected_management_context', None)
            if previous_context is not None and previous_context.operation_id == operation_id:
                await drain_thread_call(old.runtime.heartbeat)
                row = await drain_thread_call(self.store.load_document, CURRENT_MANAGEMENT, account_ref, account_ref)
                if row is None or row['document']['context'] != previous_context.to_dict() or not current_owner():
                    raise RuntimeError('SELECTED_MANAGEMENT_PREPARATION_CHANGED')
                return {'receipt': row['document'], 'replayed': True}
            if (not isinstance(old, MockAccountBundle) or not old.runtime._active or old.binding is None
                    or not current_owner()):
                raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_TARGET_RUNTIME_REQUIRED')
            if old.gateway is not None:
                await old.gateway.begin_credential_change()
            old.runtime.set_new_orders_enabled(False)
            token = uuid.uuid4().hex
            try:
                result = await drain_thread_call(self.store.prepare_selected_order_management, operation_id,
                    identity=identity, source_proof=source_proof, broker_evidence=evidence,
                    current_target_runtime=dict(account_ref=old.canonical_account_ref,
                        run_id=old.runtime.run_id, owner_token=old.runtime.owner_token),
                    new_owner_token=token, current_owner=current_owner,
                    expected_settings_revision=expected_settings_revision)
            except BaseException:
                import hashlib
                row = await drain_thread_call(self.store.load_document, CURRENT_MANAGEMENT, account_ref, account_ref)
                if (row is None or row['document']['context']['operation_id'] != operation_id
                        or row['document']['lease_owner_sha256']
                        != hashlib.sha256((packet['run_id'] + ':' + token).encode()).hexdigest()):
                    raise
                result = {'receipt': row['document'], 'replayed': True}
            context = selected_order_management_context_from_dict(result['receipt']['context'])
            candidate = None
            def manager_current():
                return (candidate is not None
                    and (candidate.runtime._active or (not self._closing and not candidate._closing
                         and credential_profile_id not in self._pending_profiles
                         and self._settings_revisions.get(credential_profile_id) == expected_settings_revision))
                    and self.active_revision(credential_profile_id) == context.credential_revision
                    and (self._bundles.get(credential_profile_id) is candidate
                         or self._contexts.get(credential_profile_id) is candidate))
            # The native token has changed. Retire and join every old task before
            # publishing or starting the new original-scope bundle.
            self._bundles.pop(credential_profile_id, None)
            await self._publish_change(credential_profile_id, None)
            await old.close(close_broker=False)
            automation = MockAutomationRuntimeContext(MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION,
                context.account_ref, context.execution_run_id, context.spec_id, context.admission_id,
                result['receipt']['initial_control_revision'], context.credential_revision)
            candidate = MockAccountBundle(self.store, settings=old.client._settings,
                account_ref=context.account_ref, run_id=context.execution_run_id,
                credential_profile_id=credential_profile_id, identity_hmac_key=self.hmac_key,
                client=old.client, broker=old.broker, identity=old._identity, binding=old.binding,
                order_transport_enabled=settings['mock_order_enabled'], require_initial_read=True,
                read_limiter=getattr(self, '_slots', None), runtime_mode=MockAccountRuntimeMode.AUTOMATIC,
                automation_context=automation, now_provider=getattr(self, '_now_provider', None),
                capture_scope=getattr(self, '_capture_scope', None), selected_management_context=context,
                selected_management_owner=manager_current, runtime_owner_token=token)
            self._contexts[credential_profile_id] = candidate
            try:
                await candidate.start()
            finally:
                # A partial broker read grants no management; keep its actual
                # reconciliation owner reachable for retry and owned shutdown.
                self._bundles[credential_profile_id] = candidate
                await self._publish_change(credential_profile_id, candidate)
            return result

        return await self.verify_selected_order_identity(packet, packet_sha256, source_proof,
            credential_profile_id=credential_profile_id, credential_revision=credential_revision,
            account_ref=account_ref, binding_revision=binding_revision,
            broker_evidence_provider=broker_evidence_provider, on_verified=prepare)

    def applied_settings_revision(self, scope: dict[str, str]):
        if scope["environment"] != "mock": return None
        bundle = next((b for b in self._bundles.values()
            if getattr(b, 'canonical_account_ref', b.account_ref) == scope["account_ref"]), None)
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
            await self._publish_change(profile_id, None)
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
        execution_run_id = (
            previous.run_id if previous is not None and previous.runtime_mode is MockAccountRuntimeMode.AUTOMATIC
            else activation.get("execution_run_id")
        )
        run_id = (activation.get("run_id") or
                  (previous.run_id if previous is not None and execution_run_id is None else None))
        if current is None:
            raise CredentialOperationError("ACCOUNT_IDENTITY_UNVERIFIED")
        if not account_ref:
            bindings = await asyncio.to_thread(self.store.load_account_bindings)
            account_ref = next((
                str(binding["account_ref"])
                for binding in reversed(bindings)
                if binding.get("credential_profile_id") == profile_id
                and binding.get("broker") == "kiwoom"
                and binding.get("environment") == "mock"
            ), None)
        if not account_ref:
            raise CredentialOperationError("ACCOUNT_IDENTITY_UNVERIFIED")
        try:
            run_id = str(uuid.UUID(str(run_id)))
        except (ValueError, TypeError, AttributeError):
            # 초기 ENV 이관 profile은 검증된 account binding은 있지만 activation
            # receipt의 UUID run_id가 없거나 임의 문자열일 수 있다. 연결 해제에는
            # 새 주문 runtime이 필요하지 않으므로 profile/account에 고정된
            # migration ID를 사용한다.
            run_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"kiwoom-monitor:legacy-mock-profile:{profile_id}:{account_ref}",
            ))
        complete_activation = {
            "operation_id", "request_id", "request_digest", "account_ref",
            "run_id", "environment", "committed_at",
        }.issubset(activation)
        if complete_activation:
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
            self._settings_revisions.get(profile_id), bool(previous and previous.monitor.is_started))
        plan.previous_disabled = current.disabled
        return ValidatedCredential(account_ref=account_ref, run_id=run_id, prepared=plan,
                                   execution_run_id=execution_run_id)

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
                # Credential activation UUID and the immutable AUTO order run
                # identify different lifetimes. Preserve both at this boundary.
                activation = current.payload.get("activation", {}) if current else {}
                execution_run_id = (
                    previous.run_id if previous is not None and previous.run_id.startswith("mock_auto_run_")
                    else activation.get("execution_run_id")
                )
                run_id = (activation.get("run_id")
                          or (previous.run_id if previous is not None and execution_run_id is None else None)
                          or str(uuid.uuid4()))
                uuid.UUID(run_id)
                if execution_run_id is not None:
                    repository = ForwardEvaluationRepository(self.store)
                    control = await drain_thread_call(repository.load_mock_automation_control, account_ref)
                    spec = (await drain_thread_call(repository.load_mock_automation_spec, account_ref, control.active_spec_id)
                            if control is not None else None)
                    admission = (await drain_thread_call(repository.load_mock_automation_admission_for_spec,
                        account_ref, control.active_spec_id) if control is not None else None)
                    if (control is None or control.execution_run_id != execution_run_id or spec is None
                            or spec.credential_profile_id != profile_id or admission is None
                            or admission.execution_run_id != execution_run_id):
                        raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
                plan = _MockCredentialPlan(client, broker, prepared, identity, previous,
                    account_ref, run_id, configuration["revision"], lock,
                    self._revisions.get(profile_id), self._settings_revisions.get(profile_id),
                    bool(previous and previous.monitor.is_started))
                plan.previous_disabled = bool(current and current.disabled)
                return ValidatedCredential(account_ref=account_ref, run_id=run_id, prepared=plan,
                                           execution_run_id=execution_run_id)
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
        await self._publish_change(profile_id, None)
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
            await self._publish_change(profile_id, None)
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
        context = None
        if candidate.execution_run_id is not None:
            repository = ForwardEvaluationRepository(self.store)
            control = await drain_thread_call(repository.load_mock_automation_control, plan.account_ref)
            admission = (await drain_thread_call(repository.load_mock_automation_admission_for_spec,
                plan.account_ref, control.active_spec_id) if control is not None else None)
            if (control is None or admission is None
                    or control.execution_run_id != candidate.execution_run_id
                    or admission.execution_run_id != candidate.execution_run_id):
                raise RuntimeError("MOCK_AUTOMATION_RUNTIME_CONTEXT_MISMATCH")
            context = MockAutomationRuntimeContext(MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION,
                plan.account_ref, candidate.execution_run_id, control.active_spec_id,
                admission.admission_id, control.control_revision, record.revision)
        bundle = MockAccountBundle(self.store, settings=plan.prepared.settings,
            account_ref=plan.account_ref, run_id=candidate.execution_run_id or plan.run_id,
            credential_profile_id=profile_id,
            identity_hmac_key=self.hmac_key, order_transport_enabled=configuration["mock_order_enabled"],
            client=plan.client, broker=plan.broker, identity=plan.identity, binding=binding,
            require_initial_read=True, read_limiter=self._slots, now_provider=self._now_provider,
            capture_scope=self._capture_scope,
            runtime_mode=MockAccountRuntimeMode.AUTOMATIC if context is not None else MockAccountRuntimeMode.MANUAL,
            automation_context=context)
        self._contexts[profile_id] = bundle  # Retain failed committed transport for explicit recovery.
        try:
            if self._closing: raise RuntimeError("MOCK_OWNER_CLOSED")
            bundle.set_position_order_observation_account(self._position_order_observation_account)
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
        await self._publish_change(profile_id, bundle)

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
            await self._publish_change(profile_id, None)
            plan.resumed = True
            return
        if plan.broker._credential_paused: await plan.broker.end_credential_change()
        previous = plan.previous
        if previous is not None and plan.previous_revision is not None and not self._closing:
            restored = MockAccountBundle(self.store, settings=plan.client._settings,
                account_ref=previous.account_ref, run_id=previous.run_id, credential_profile_id=profile_id,
                identity_hmac_key=self.hmac_key, order_transport_enabled=previous.gateway is not None,
                client=plan.client, broker=plan.broker, identity=previous._identity, binding=previous.binding,
                require_initial_read=True, read_limiter=self._slots, now_provider=self._now_provider,
                capture_scope=self._capture_scope, runtime_mode=previous.runtime_mode,
                automation_context=previous.automation_context)
            try:
                if plan.previous_monitor_enabled:
                    restored.set_position_order_observation_account(self._position_order_observation_account)
                    await restored.start()
            except Exception:
                await restored.close(close_broker=False)
                await plan.broker.begin_credential_change()
                raise
            self._contexts[profile_id] = restored; self._bundles[profile_id] = restored
            self._revisions[profile_id] = plan.previous_revision
            self._settings_revisions[profile_id] = plan.previous_settings_revision
            await self._publish_change(profile_id, restored)
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
        lock = self._account_locks.setdefault(getattr(old, 'canonical_account_ref', old.account_ref), asyncio.Lock())
        if self._closing or lock.locked() or profile_id in self._pending_profiles or profile_id in self._booting:
            raise CredentialOperationError("PROFILE_BUSY")
        await lock.acquire()
        self._pending_profiles.add(profile_id)
        new = None
        saved = None
        credential_revision = record.revision
        rollback = _MockCredentialPlan(old.client, old.broker, None, old._identity, old,
            old.account_ref, old.run_id, expected_revision, lock, self._revisions.get(profile_id),
            self._settings_revisions.get(profile_id), old.monitor.is_started)
        self._applying[profile_id] = rollback
        try:
            if ({**value, "scope": current["scope"], "revision": expected_revision} == current
                    and self._settings_revisions.get(profile_id) == expected_revision):
                return {"settings": current, "applied_revision": self._settings_revisions.get(profile_id)}
            selected_management = getattr(old, 'selected_management_context', None) is not None
            self._bundles.pop(profile_id, None)
            if not selected_management:
                self._revisions.pop(profile_id, None)
                self._settings_revisions.pop(profile_id, None)
            await self._publish_change(profile_id, None)
            await old.close(close_broker=False)
            if selected_management:
                self._revisions.pop(profile_id, None)
                self._settings_revisions.pop(profile_id, None)
            await old.broker.begin_credential_change()
            saved = await asyncio.to_thread(self.store.save_account_settings,
                {**value, "scope": scope}, expected_revision=expected_revision)
            if self._closing: raise RuntimeError("MOCK_OWNER_CLOSED")
            await old.broker.end_credential_change()
            new = MockAccountBundle(self.store, settings=old.client._settings,
                account_ref=getattr(old, 'canonical_account_ref', old.account_ref),
                run_id=uuid.uuid4().hex if selected_management else old.run_id,
                credential_profile_id=profile_id, identity_hmac_key=self.hmac_key,
                order_transport_enabled=saved["mock_order_enabled"], client=old.client, broker=old.broker,
                identity=old._identity, binding=old.binding, require_initial_read=True, read_limiter=self._slots,
                now_provider=self._now_provider, capture_scope=self._capture_scope,
                runtime_mode=MockAccountRuntimeMode.MANUAL if selected_management else old.runtime_mode,
                automation_context=None if selected_management else old.automation_context)
            self._contexts[profile_id] = new
            new.set_position_order_observation_account(self._position_order_observation_account)
            if saved["monitor_enabled"]: await new.start()
            latest = await asyncio.to_thread(self.store.load_account_settings, scope)
            if self._closing or latest["revision"] != saved["revision"]:
                raise RuntimeError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
            self._bundles[profile_id] = new
            self._revisions[profile_id] = credential_revision
            self._settings_revisions[profile_id] = saved["revision"]
            self.errors.pop(profile_id, None)
            await self._publish_change(profile_id, new)
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

    async def require_execution_settings(self, profile_id, *, expected_settings_revision, credential_revision):
        """Keep the existing saved/applied settings fence for either runtime mode."""
        bundle = self._bundles.get(profile_id)
        if bundle is None or bundle.binding is None or self._closing:
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
        settings = await drain_thread_call(self.store.load_account_settings, bundle.binding.scope.to_dict())
        if (settings["revision"] != expected_settings_revision
                or self._settings_revisions.get(profile_id) != expected_settings_revision
                or self._revisions.get(profile_id) != credential_revision):
            raise CredentialOperationError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
        if not settings["monitor_enabled"] or not settings["mock_order_enabled"]:
            raise CredentialOperationError("MOCK_ORDER_TRANSPORT_DISABLED", 409)
        return settings

    async def switch_execution_mode(
        self,
        profile_id: str,
        mode: MockAccountRuntimeMode,
        *,
        expected_settings_revision: int,
        credential_revision: int,
        automation_context: MockAutomationRuntimeContext | None = None,
    ) -> "MockAccountBundle":
        """Replace one drained account bundle without sharing its immutable runtime owner."""
        old = self._bundles.get(profile_id)
        if old is None or old.binding is None or self._closing:
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
        if (mode is MockAccountRuntimeMode.AUTOMATIC) != (automation_context is not None):
            raise CredentialOperationError("ACCOUNT_RUNTIME_MODE_INVALID", 422)
        if automation_context is not None and (
            automation_context.account_ref != old.account_ref
            or automation_context.credential_revision != credential_revision
        ):
            raise CredentialOperationError("ACCOUNT_RUNTIME_CONTEXT_MISMATCH", 409)
        scope = self._scope(old.account_ref)
        settings = await self.require_execution_settings(profile_id,
            expected_settings_revision=expected_settings_revision, credential_revision=credential_revision)
        lock = self._account_locks.setdefault(old.account_ref, asyncio.Lock())
        if lock.locked() or profile_id in self._pending_profiles or profile_id in self._booting:
            raise CredentialOperationError("PROFILE_BUSY")
        await lock.acquire()
        self._pending_profiles.add(profile_id)
        try:
            if old.gateway is not None:
                await old.gateway.begin_credential_change()
            recovery = await old.monitor.refresh_recovery()
            if (
                recovery.account.reserved_open_buy_won
                or any(int(quantity) > 0 for quantity in recovery.account.positions.values())
                or any(order.state.value not in {"FILLED", "CANCELLED", "REJECTED"} for order in recovery.orders)
                or old.runtime.load_active_intents()
            ):
                raise CredentialOperationError("ACCOUNT_RUNTIME_SWITCH_REQUIRES_FLAT", 409)
            self._bundles.pop(profile_id, None)
            await self._publish_change(profile_id, None)
            await old.close(close_broker=False)
            latest = await drain_thread_call(self.store.load_account_settings, scope)
            if latest["revision"] != expected_settings_revision:
                raise RuntimeError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
            run_id = automation_context.execution_run_id if automation_context else old.run_id
            new = MockAccountBundle(
                self.store,
                settings=old.client._settings,
                account_ref=old.account_ref,
                run_id=run_id,
                credential_profile_id=profile_id,
                identity_hmac_key=self.hmac_key,
                order_transport_enabled=True,
                client=old.client,
                broker=old.broker,
                identity=old._identity,
                binding=old.binding,
                require_initial_read=True,
                read_limiter=self._slots,
                runtime_mode=mode,
                automation_context=automation_context,
                now_provider=self._now_provider,
                capture_scope=self._capture_scope,
            )
            self._contexts[profile_id] = new
            new.set_position_order_observation_account(self._position_order_observation_account)
            await new.start()
            latest = await drain_thread_call(self.store.load_account_settings, scope)
            if latest["revision"] != expected_settings_revision:
                await new.close(close_broker=False)
                raise RuntimeError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
            self._bundles[profile_id] = new
            await self._publish_change(profile_id, new)
            return new
        except CredentialOperationError:
            if old.runtime._active and old.gateway is not None:
                old.gateway.end_credential_change()
            raise
        except Exception:
            self.errors[profile_id] = "ACCOUNT_SETTINGS_RECOVERY_REQUIRED"
            raise CredentialOperationError("ACCOUNT_SETTINGS_RECOVERY_REQUIRED", 503) from None
        finally:
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
        runtime_mode: MockAccountRuntimeMode = MockAccountRuntimeMode.MANUAL,
        automation_context: MockAutomationRuntimeContext | None = None,
        now_provider: Callable[[], datetime] | None = None,
        capture_scope=None,
        selected_management_context=None,
        selected_management_owner=None,
        runtime_owner_token: str | None = None,
    ) -> None:
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient

        if settings.environment != "mock":
            raise ValueError("MOCK_ENVIRONMENT_REQUIRED")
        if not account_ref or not run_id or not credential_profile_id:
            raise ValueError("MOCK_ACCOUNT_CONTEXT_REQUIRED")
        self._account_ref = account_ref
        self._run_id = run_id
        self._credential_profile_id = credential_profile_id
        from kiwoom_monitor.domain.selected_order_evidence import (
            SelectedOrderManagementContext, selected_order_management_context_from_dict)
        management = (selected_management_context if isinstance(selected_management_context, SelectedOrderManagementContext)
            else selected_order_management_context_from_dict(selected_management_context)
            if selected_management_context is not None else None)
        if management is not None and (
                (management.account_ref, management.execution_run_id, management.credential_profile_id)
                != (account_ref, run_id, credential_profile_id)
                or identity is None or binding is None or not callable(selected_management_owner)
                or not runtime_owner_token or runtime_mode is not MockAccountRuntimeMode.AUTOMATIC):
            raise ValueError('SELECTED_MANAGEMENT_BUNDLE_CONTEXT_INVALID')
        self._selected_management_context = management
        self._selected_management_owner = selected_management_owner
        if (runtime_mode is MockAccountRuntimeMode.AUTOMATIC) != (automation_context is not None):
            raise ValueError("MOCK_RUNTIME_MODE_CONTEXT_INVALID")
        if automation_context is not None and (
            automation_context.account_ref != account_ref
            or automation_context.execution_run_id != run_id
        ):
            raise ValueError("MOCK_RUNTIME_MODE_CONTEXT_INVALID")
        self._runtime_mode = runtime_mode
        self._automation_context = automation_context
        self._store = store
        self._hmac_key = identity_hmac_key
        if (client is None) != (broker is None) or (identity is None) != (binding is None):
            raise ValueError("MOCK_BUNDLE_CONTEXT_INVALID")
        if binding is not None and (binding.scope.account_ref != (management.canonical_account_ref if management else account_ref)
                or binding.credential_profile_id != credential_profile_id
                or binding.scope.environment != AccountEnvironment.MOCK
                or identity.environment != AccountEnvironment.MOCK):
            raise ValueError("MOCK_BUNDLE_CONTEXT_INVALID")
        self._identity = identity
        self._binding = binding
        self._require_initial_read = require_initial_read
        self._closing = False
        self._position_order_observation_account = None
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
        if management is not None:
            self.repository.bind_selected_management_context(management, current_owner=selected_management_owner)
        now = ((lambda: as_kst(now_provider())) if now_provider is not None
               else (lambda: as_kst(self.client.server_now())))
        transport = make_mock_transport(self.client, now_provider=now) if order_transport_enabled else None
        self.runtime = ExecutionRuntime(
            OrderLifecycle(self.repository, transport, now_provider=now), self.repository,
            account_ref=account_ref, run_id=run_id, owner_token=runtime_owner_token or uuid.uuid4().hex,
            now_provider=now if now_provider is not None else (lambda: datetime.now(KST)),
        )
        if runtime_mode is MockAccountRuntimeMode.AUTOMATIC:
            self.runtime.set_new_orders_enabled(False)
        self.monitor = MockAccountMonitor(
            KiwoomMockAccountReader(
                self.broker, environment="mock", account_ref=account_ref, now_provider=now,
            ), self.runtime, self.repository, now_provider=now, read_limiter=read_limiter,
            automation_risk_repository=(
                ForwardEvaluationRepository(store, selected_management_context=management)
                if runtime_mode is MockAccountRuntimeMode.AUTOMATIC else None
            ),
            automation_binding_revision=(binding.binding_revision if binding is not None else 0),
            automation_account_scope=(AccountScope('kiwoom', AccountEnvironment.MOCK, account_ref) if management is not None
                                      else binding.scope if binding is not None else None),
            new_orders_enabled_on_start=(runtime_mode is MockAccountRuntimeMode.MANUAL),
            capture_scope=capture_scope,
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
            automation_execution_cut=lambda: self.monitor.latest_automation_execution_cut,
        ) if order_transport_enabled else None
        if self.gateway is not None:
            self.gateway.set_manual_submit_enabled(runtime_mode is MockAccountRuntimeMode.MANUAL)

    @property
    def account_ref(self) -> str:
        return self._account_ref

    @property
    def canonical_account_ref(self) -> str:
        return self._selected_management_context.canonical_account_ref if self._selected_management_context is not None else self._account_ref

    @property
    def selected_management_context(self):
        return self._selected_management_context

    def set_position_order_observation_account(self, account_ref):
        self._position_order_observation_account = account_ref
        self.monitor.set_account_order_observation_enabled(
            self._binding is not None and self._binding.scope.environment.value == "mock"
            and account_ref == self.account_ref)

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def credential_profile_id(self) -> str:
        return self._credential_profile_id

    @property
    def runtime_mode(self) -> MockAccountRuntimeMode:
        return self._runtime_mode

    @property
    def automation_context(self) -> MockAutomationRuntimeContext | None:
        return self._automation_context

    def refresh_automation_control(self, control) -> None:
        """Reflect an acknowledged control CAS in this bundle's existing fence."""
        context = self._automation_context
        if (context is None or control.account_ref != self.account_ref
                or control.execution_run_id != self.run_id or control.active_spec_id != context.spec_id
                or control.control_revision < context.control_revision):
            raise RuntimeError("MOCK_AUTOMATION_RUNTIME_CONTEXT_MISMATCH")
        self._automation_context = replace(context, control_revision=control.control_revision)

    @property
    def binding(self):
        return self._binding

    def _resolve_scope(self, raw_account_number: str):
        if (self._closing or self._identity is None or self._binding is None
                or self._selected_management_context is not None and not self._selected_management_owner()):
            return None
        try:
            fingerprint = account_identity_fingerprint(
                raw_account_number, AccountEnvironment.MOCK, self._hmac_key,
            )
        except ValueError:
            return None
        return (AccountScope('kiwoom', AccountEnvironment.MOCK, self.account_ref)
                if self._selected_management_context is not None else self._binding.scope) if hmac.compare_digest(
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
        self.set_position_order_observation_account(self._position_order_observation_account)
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
