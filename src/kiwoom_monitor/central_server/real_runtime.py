"""Real credential activation and scoped historical-query ownership.

The market context reuses the server's single collector/broker. Additional
accounts own read-only brokers, never another TOP20 or market WebSocket.
"""
from __future__ import annotations

import asyncio
import hmac
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kiwoom_monitor.application.account_identity import (
    bind_verified_account_identity, prepare_verified_account_identity,
)
from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import KiwoomAccountIdentityReader

from .account_query import AccountQuerySessionManager
from .credential_runtime import CredentialOperationError, CredentialRuntimeHooks, ValidatedCredential
from .market_observations import as_kst
from .rest_broker import CentralRestBroker, ACCOUNT_RECOVERY_ENDPOINTS


@dataclass
class RealAccountContext:
    client: Any = field(repr=False)
    broker: Any = field(repr=False)
    binding: AccountBinding | None = None
    identity: Any = field(default=None, repr=False)
    run_id: str | None = None
    account_queries: Any = field(default=None, repr=False)
    account_reads: set = field(default_factory=set, repr=False)
    account_reads_paused: bool = True
    monitor_task: Any = field(default=None, repr=False)
    monitor_stop: Any = field(default=None, repr=False)
    monitor_revision: int | None = None
    monitor_binding_revision: int | None = None
    monitor_enabled: bool = False
    monitor_last_success_at: str | None = None
    monitor_error_code: str | None = None
    monitor_wake: Any = field(default=None, repr=False)
    realtime: Any = field(default=None, repr=False)
    event_queue: Any = field(default_factory=lambda: asyncio.Queue(maxsize=1000), repr=False)
    event_writer: Any = field(default=None, repr=False)
    pending_event: Any = field(default=None, repr=False)
    event_error_code: str | None = None
    dropped_events: int = 0
    last_event_at: str | None = None
    monitor_generation: int = 0
    realtime_drain_failed: bool = False


@dataclass
class _RealCredentialPlan:
    context: RealAccountContext = field(repr=False)
    prepared: Any = field(repr=False)
    identity: Any = field(repr=False)
    previous: RealAccountContext | None = field(repr=False)
    account_ref: str
    run_id: str
    settings_revision: int
    lock: asyncio.Lock = field(repr=False)
    previous_revision: int | None = None
    previous_settings_revision: int | None = None
    previous_disabled: bool = False
    committed: bool = False
    published: bool = False
    drained: bool = False
    resumed: bool = False


@dataclass(frozen=True)
class _MarketRolePlan:
    source_profile_id: str
    target_profile_id: str
    expected_revision: int
    expected_binding_revision: int
    source_credential_revision: int
    target_credential_revision: int
    source: RealAccountContext = field(repr=False, compare=False)
    target: RealAccountContext = field(repr=False, compare=False)


class RealCredentialOwner:
    """Own real profile admission, account exclusion and market rotation barriers."""

    market_profile_id = "nas-real-default"

    def __init__(self, store, vault, *, hmac_key: bytes, market_client=None,
                 market_broker=None, market_collector=None, on_change=None, account_poll_interval=30.0,
                 account_realtime_factory=None, account_event_publisher=None):
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient
        self.store, self.vault, self.hmac_key = store, vault, hmac_key
        self.collector = market_collector
        self._market_broker = market_broker
        self._market_role_revision = None
        self._role_tasks = set()
        self._settings_tasks = set()
        if account_poll_interval <= 0:
            raise ValueError("account_poll_interval must be positive")
        self._account_poll_interval = account_poll_interval
        if account_realtime_factory is None:
            from .mock_account_monitor import RealAccountRealtimeCollector
            account_realtime_factory = RealAccountRealtimeCollector
        self._account_realtime_factory = account_realtime_factory
        self._account_event_publisher = account_event_publisher or (lambda *_: None)
        self._market_role_recovery = False
        self._contexts = {}
        if market_client is not None:
            self.market_profile_id = store.load_market_profile_settings()["market_profile_id"]
            self._contexts[self.market_profile_id] = RealAccountContext(market_client, market_broker)
            self.store.register_credential_profile("kiwoom_real", self.market_profile_id,
                                                   datetime.now().astimezone().isoformat())
        self._bundles = {}
        self._revisions = {}
        self._settings_revisions = {}
        self._disabled_scopes = {}
        self._pending = set()
        self._market_role_reservation = None
        self._market_role_idle = asyncio.Event()
        self._market_role_idle.set()
        self._locks = {}
        self._applying = {}
        self._slots = asyncio.Semaphore(2)
        self._probe_lock = asyncio.Lock()
        self._probe = CentralRestBroker(KiwoomRestClient(KiwoomSettings("", "", "real")),
            allowed_endpoints={"ka00001": "/api/dostk/acnt"}, namespace="real-credential-probe")
        self._on_change = on_change or (lambda *_: None)
        self._start_task = self._close_task = None
        self._boot_tasks = []
        self._closing = False
        self.errors = {}

    def hooks(self):
        return CredentialRuntimeHooks(self.prepare, self._unused_drain, self.publish, self.resume,
            self.active_revision, self.drain, self.release, self.begin_commit)

    async def _unused_drain(self, profile_id):
        raise RuntimeError("REAL_CANDIDATE_REQUIRED")

    def bundle(self, profile_id):
        return self._bundles.get(profile_id)

    def account_bindings(self):
        return tuple(context.binding for context in self._bundles.values() if context.binding is not None)

    def active_revision(self, profile_id):
        return self._revisions.get(profile_id)

    def applied_market_role_revision(self):
        return self._market_role_revision

    async def read_account(self, profile_id, *, expected_binding_revision):
        context = self.bundle(profile_id)
        if (self._closing or self._market_role_recovery or context is None or context.account_reads_paused
                or context.binding is None):
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
        if type(expected_binding_revision) is not int or context.binding.binding_revision != expected_binding_revision:
            raise CredentialOperationError("ACCOUNT_CONTEXT_MISMATCH")
        if len(context.account_reads) >= 1:
            raise CredentialOperationError("PROFILE_BUSY")
        task = asyncio.create_task(self._read_account(context), name="real-account-read")
        context.account_reads.add(task)
        def completed(done):
            context.account_reads.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def _read_account(self, context):
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import KiwoomRealAccountReader
        reader = KiwoomRealAccountReader(context.broker, environment="real",
            account_ref=context.binding.scope.account_ref, now_provider=lambda: as_kst(context.client.server_now()))
        if context.broker is self._market_broker:
            return await reader.read()
        async with self._slots:
            return await reader.read()

    async def _drain_account_reads(self, *contexts, require_saved_events=True):
        for context in contexts:
            context.account_reads_paused = True
            if context.monitor_stop is not None:
                context.monitor_stop.set()
            if context.monitor_wake is not None:
                context.monitor_wake.set()
        # A complete recovery plus its owned DB write finishes before routing changes.
        monitor_results = await asyncio.gather(*(asyncio.shield(context.monitor_task) for context in contexts
                                                 if context.monitor_task is not None), return_exceptions=True)
        writer_results = await asyncio.gather(*(asyncio.shield(context.event_writer) for context in contexts
                                                if context.event_writer is not None), return_exceptions=True)
        for context in contexts:
            if context.monitor_task is not None and context.monitor_task.done() and (
                    context.monitor_task.cancelled() or context.monitor_task.exception() is not None):
                context.realtime_drain_failed = True
        await asyncio.gather(*(asyncio.shield(task) for context in contexts
                               for task in tuple(context.account_reads)), return_exceptions=True)
        if require_saved_events and not self._closing and any(isinstance(result, BaseException)
                                                               for result in (*monitor_results, *writer_results)):
            raise RuntimeError("REAL_ACCOUNT_DRAIN_FAILED")
        if require_saved_events and not self._closing and any(context.pending_event is not None or not context.event_queue.empty()
                                     for context in contexts):
            raise RuntimeError("REAL_ACCOUNT_EVENTS_PENDING")

    def _start_account_monitor(self, profile_id, context, configuration):
        if context.realtime_drain_failed:
            raise RuntimeError("REAL_ACCOUNT_DRAIN_FAILED")
        if context.monitor_task is not None and not context.monitor_task.done():
            raise RuntimeError("REAL_ACCOUNT_MONITOR_ALREADY_RUNNING")
        context.monitor_enabled = configuration["monitor_enabled"]
        context.monitor_generation += 1
        if context.monitor_binding_revision != context.binding.binding_revision:
            context.monitor_last_success_at = context.monitor_error_code = None
            context.last_event_at = context.event_error_code = None
            context.dropped_events = 0
        context.monitor_binding_revision = context.binding.binding_revision
        context.monitor_revision = configuration["revision"]
        context.monitor_task = None
        context.monitor_stop = asyncio.Event()
        context.monitor_wake = asyncio.Event()
        context.realtime = None
        if context.monitor_enabled and not self._closing and not context.account_reads_paused:
            binding = context.binding
            generation = context.monitor_generation
            if profile_id != self.market_profile_id:
                async def token():
                    async with self._slots:
                        return await asyncio.to_thread(context.client.get_access_token)
                context.realtime = self._account_realtime_factory(
                    token_provider=context.client.get_access_token,
                    async_token_provider=token,
                    now_provider=lambda: as_kst(context.client.server_now()),
                    account_scope_resolver=lambda raw: self._resolve_account_scope(context, binding, raw),
                    event_handler=lambda name, value: self._handle_account_event(
                        profile_id, context, binding, name, value, expected_generation=generation))
            context.event_writer = asyncio.create_task(self._write_account_events(context), name="real-account-event-writer")
            context.monitor_task = asyncio.create_task(self._monitor_account(profile_id, context),
                                                       name="real-account-monitor")

    def _resolve_account_scope(self, context, binding, raw):
        from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import account_identity_fingerprint
        if self.bundle(binding.credential_profile_id) is not context or context.binding != binding:
            return None
        try:
            fingerprint = account_identity_fingerprint(raw, AccountEnvironment.REAL, self.hmac_key)
        except ValueError:
            return None
        return binding.scope if hmac.compare_digest(fingerprint, context.identity.identity_fingerprint) else None

    def on_market_account_event(self, name, value):
        context = self.bundle(self.market_profile_id)
        if context is not None:
            self._handle_account_event(self.market_profile_id, context, context.binding, name, value)

    def _handle_account_event(self, profile_id, context, binding, name, value, *, expected_generation=None):
        if (self._closing or self.bundle(profile_id) is not context or context.binding != binding
                or context.account_reads_paused or not context.monitor_enabled or context.monitor_stop.is_set()):
            return
        if expected_generation is not None and expected_generation != context.monitor_generation:
            return
        scope = value if isinstance(value, AccountScope) else getattr(value, "origin_scope", None)
        if name not in {"connected", "disconnected"} and scope != binding.scope:
            return
        if name in {"order_execution", "account_balance"}:
            received_at = as_kst(context.client.server_now())
            try:
                context.event_queue.put_nowait((binding, context.monitor_revision, name, value, received_at))
            except asyncio.QueueFull:
                context.dropped_events += 1
                context.event_error_code = "REAL_ACCOUNT_EVENT_QUEUE_FULL"
            if context.broker is not self._market_broker:
                try:
                    self._account_event_publisher(name, value)
                except Exception:
                    context.event_error_code = "REAL_ACCOUNT_EVENT_DELIVERY_FAILED"
        if name in {"connected", "disconnected", "order_changed", "account_balance_changed",
                    "order_execution", "account_balance"}:
            context.monitor_wake.set()

    async def _write_account_events(self, context):
        stop = context.monitor_stop
        while context.pending_event is not None or not context.event_queue.empty() or not stop.is_set():
            if context.pending_event is None:
                try:
                    context.pending_event = await asyncio.wait_for(context.event_queue.get(), 0.25)
                except asyncio.TimeoutError:
                    continue
            binding, revision, name, value, received_at = context.pending_event
            try:
                await asyncio.to_thread(self.store.save_real_account_event, binding, name, value, received_at,
                                        settings_revision=revision)
                context.pending_event = None
                context.last_event_at = received_at.isoformat()
                if not context.dropped_events:
                    context.event_error_code = None
            except Exception:
                context.event_error_code = "REAL_ACCOUNT_EVENT_STORE_FAILED"
                if stop.is_set():
                    return  # Retain the pending event; a live transition cannot commit.
                try:
                    await asyncio.wait_for(stop.wait(), self._account_poll_interval)
                except asyncio.TimeoutError:
                    pass

    async def _restart_account_monitors(self, *profile_ids):
        for profile_id in profile_ids:
            context = self.bundle(profile_id)
            if context is not None:
                configuration = await asyncio.to_thread(self.store.load_account_settings, context.binding.scope.to_dict())
                if configuration["active_profile_id"] != profile_id:
                    raise RuntimeError("ACCOUNT_PROFILE_CONFLICT")
                self._start_account_monitor(profile_id, context, configuration)

    async def _monitor_account(self, profile_id, context):
        realtime = context.realtime
        try:
            if realtime is not None:
                try:
                    await realtime.start()
                except Exception:
                    context.event_error_code = "ACCOUNT_WS_START_FAILED"
            await self._monitor_account_cycles(profile_id, context)
        finally:
            if realtime is not None:
                await realtime.close()

    async def _monitor_account_cycles(self, profile_id, context):
        # Leave bootstrap's first ranking request ahead of supplemental account reads.
        stop, wake, binding, revision = context.monitor_stop, context.monitor_wake, context.binding, context.monitor_revision
        while not stop.is_set():
            try:
                await asyncio.wait_for(wake.wait(), self._account_poll_interval)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                break
            if wake.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), 0.5)
                    break
                except asyncio.TimeoutError:
                    pass
                wake.clear()
            try:
                recovery = await self.read_account(profile_id, expected_binding_revision=binding.binding_revision)
                received_at = as_kst(context.client.server_now())
                await asyncio.to_thread(self.store.save_real_account_recovery, binding, recovery,
                                        received_at, settings_revision=revision)
                context.monitor_last_success_at = received_at.isoformat()
                context.monitor_error_code = None
            except CredentialOperationError as error:
                # A manual read owns the current cycle; skip rather than duplicate it.
                if error.code != "PROFILE_BUSY" and not stop.is_set():
                    context.monitor_error_code = "REAL_ACCOUNT_COLLECTION_FAILED"
            except Exception:
                context.monitor_error_code = "REAL_ACCOUNT_COLLECTION_FAILED"

    def account_monitor_status(self, scope):
        context = next((value for value in self._contexts.values()
                        if value.binding is not None and value.binding.scope.to_dict() == scope), None)
        if context is None:
            return {"mode": "rest_poll", "state": "unavailable", "poll_interval_seconds": self._account_poll_interval,
                    "last_success_at": None, "error_code": None}
        state = ("paused" if context.account_reads_paused else "off" if not context.monitor_enabled
                 else "collecting" if context.account_reads else "waiting")
        if context.monitor_enabled and not context.account_reads_paused and (
                context.monitor_task is None or context.monitor_task.done()):
            state = "unavailable"
        shared = context.broker is self._market_broker
        ready = ((getattr(getattr(self.collector, "_hub", None), "upstream_ready", False) is True) if shared
                 else getattr(context.realtime, "ready", False) is True)
        ws_state = ("off" if not context.monitor_enabled else "paused" if context.account_reads_paused
                    else "ready" if ready else "waiting")
        return {"mode": "rest_poll", "state": state, "poll_interval_seconds": self._account_poll_interval,
                "last_success_at": context.monitor_last_success_at, "error_code": context.monitor_error_code,
                "realtime": {"source": "shared_market" if shared else "account_only", "state": ws_state,
                             "last_event_at": context.last_event_at, "dropped_events": context.dropped_events,
                             "error_code": "REAL_ACCOUNT_DRAIN_FAILED" if context.realtime_drain_failed else context.event_error_code or (
                                 getattr(context.realtime, "error_code", None) if not shared and context.realtime else None)}}

    async def change_market_role(self, profile_id, *, expected_revision, expected_binding_revision):
        if self._closing:
            raise CredentialOperationError("PROFILE_BUSY")
        task = asyncio.create_task(self._change_market_role(profile_id, expected_revision,
                                    expected_binding_revision), name="real-market-role-change")
        self._role_tasks.add(task)
        def completed(done):
            self._role_tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def _change_market_role(self, profile_id, expected_revision, expected_binding_revision):
        async with self.market_role_change(profile_id, expected_revision=expected_revision,
                                           expected_binding_revision=expected_binding_revision) as plan:
            if plan.source is plan.target:
                await self.validate_market_role(plan)
                return await asyncio.to_thread(self.store.load_market_profile_settings)
            source, target = plan.source, plan.target
            brokers = source.broker, target.broker
            committing = False
            collector_started = False
            try:
                await self._drain_account_reads(source, target)
                for context in (source, target):
                    await context.account_queries.begin_credential_change()
                if self.collector is not None:
                    collector_started = True
                    await self.collector.begin_credential_change()
                for broker in brokers:
                    await broker.begin_credential_change()
                await self.validate_market_role(plan)
                committing = True  # An unknown write outcome must stay fenced.
                try:
                    settings = await asyncio.to_thread(self.store.save_market_profile_settings,
                        {"market_profile_id": profile_id, "expected_binding_revision": expected_binding_revision},
                        expected_revision=expected_revision)
                except ValueError:
                    committing = False  # Store validation/CAS errors roll back.
                    raise
                brokers[0].swap_drained_clients(brokers[1])
                source.broker, target.broker = brokers[1], brokers[0]
                source.broker._namespace = f"real-account:{source.binding.scope.account_ref}"
                source.account_queries.rebind_drained_broker(source.broker, read_limiter=self._slots)
                target.account_queries.rebind_drained_broker(target.broker)
                self.market_profile_id = profile_id
                for broker in brokers:
                    await broker.end_credential_change()
                for context in (source, target):
                    context.account_queries.end_credential_change(invalidate_cursors=False)
                    context.account_reads_paused = False
                await self._restart_account_monitors(plan.source_profile_id, profile_id)
                self._on_change(plan.source_profile_id, source)
                self._on_change(profile_id, target)
                if self.collector is not None:
                    await self.collector.end_credential_change()
                self._market_role_revision = settings["revision"]
                return settings
            except Exception as error:
                self._market_role_revision = None
                if committing:
                    await self._fence_market_role(plan, brokers)
                    raise CredentialOperationError("MARKET_ROLE_RECOVERY_REQUIRED", 503) from None
                try:
                    for broker in brokers:
                        if broker._credential_paused:
                            await broker.end_credential_change()
                    for context in (source, target):
                        if context.account_queries._credential_paused:
                            context.account_queries.end_credential_change(invalidate_cursors=False)
                        context.account_reads_paused = False
                    await self._restart_account_monitors(plan.source_profile_id, profile_id)
                    if collector_started:
                        await self.collector.end_credential_change()
                    self._market_role_revision = expected_revision
                except Exception:
                    await self._fence_market_role(plan, brokers)
                    raise CredentialOperationError("MARKET_ROLE_RECOVERY_REQUIRED", 503) from None
                if isinstance(error, CredentialOperationError):
                    raise error
                if isinstance(error, ValueError) and str(error) in {
                        "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT", "MARKET_PROFILE_UNAVAILABLE",
                        "ACCOUNT_CONTEXT_MISMATCH", "ACCOUNT_IDENTITY_UNVERIFIED",
                        "MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED", "ACCOUNT_SETTINGS_RECOVERY_REQUIRED"}:
                    raise CredentialOperationError(str(error)) from None
                raise CredentialOperationError("MARKET_ROLE_CHANGE_FAILED", 503) from None

    async def _fence_market_role(self, plan, brokers):
        self._market_role_recovery = True
        await self._drain_account_reads(plan.source, plan.target, require_saved_events=False)
        for candidate_id in (plan.source_profile_id, plan.target_profile_id):
            self._bundles.pop(candidate_id, None)
            self._revisions.pop(candidate_id, None)
            self._on_change(candidate_id, None)
            self.errors[candidate_id] = "MARKET_ROLE_RECOVERY_REQUIRED"
        operations = [context.account_queries.begin_credential_change() for context in (plan.source, plan.target)]
        operations.extend(broker.begin_credential_change() for broker in brokers)
        if self.collector is not None:
            operations.append(self.collector.begin_credential_change())
        await asyncio.gather(*operations, return_exceptions=True)

    def applied_settings_revision(self, scope):
        if scope["environment"] != "real":
            return None
        # This revision proves REST polling policy, not WS registration or a successful read.
        for context in self._bundles.values():
            if context.binding.scope.to_dict() == scope and not context.account_reads_paused:
                if (not context.monitor_enabled or (context.monitor_task is not None and not context.monitor_task.done())):
                    return context.monitor_revision
        for profile_id, disabled_scope in self._disabled_scopes.items():
            if disabled_scope == scope:
                return self._settings_revisions.get(profile_id)
        return None

    def begin_commit(self, profile_id, candidate):
        candidate.prepared.committed = True

    async def update_settings(self, scope, value, expected_revision):
        if self._closing:
            raise CredentialOperationError("PROFILE_BUSY")
        task = asyncio.create_task(self._update_settings(scope, value, expected_revision),
                                   name="real-account-settings-apply")
        self._settings_tasks.add(task)
        task.add_done_callback(self._settings_tasks.discard)
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(task)

    async def _update_settings(self, scope, value, expected_revision):
        if (not isinstance(value, dict) or set(value) != {"active_profile_id", "monitor_enabled", "mock_order_enabled"}
                or type(expected_revision) is not int or not 0 <= expected_revision < 2**63
                or type(value["monitor_enabled"]) is not bool or value["mock_order_enabled"] is not False
                or not isinstance(scope, dict) or scope.get("environment") != "real" or scope.get("broker") != "kiwoom"):
            raise CredentialOperationError("ACCOUNT_SETTINGS_INVALID", 422)
        if self._closing or self._market_role_reservation is not None or self._market_role_recovery:
            raise CredentialOperationError("PROFILE_BUSY")
        # Reserve the role barrier before loading the profile; individual credential
        # candidates are checked again before this account's lock is acquired.
        reservation = object()
        self._pending.add(reservation)
        profile_id, lock, context = None, None, None
        committing = False
        drained = False
        current = None
        try:
            current = await asyncio.to_thread(self.store.load_account_settings, scope)
            profile_id = current["active_profile_id"]
            if current["revision"] != expected_revision:
                raise CredentialOperationError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
            if profile_id is None or value["active_profile_id"] != profile_id:
                raise CredentialOperationError("ACCOUNT_PROFILE_UNAVAILABLE")
            context = self.bundle(profile_id)
            if context is None or context.binding.scope.to_dict() != scope or context.account_reads_paused:
                raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
            if self._closing or profile_id in self._pending:
                raise CredentialOperationError("PROFILE_BUSY")
            lock = self._locks.setdefault(scope["account_ref"], asyncio.Lock())
            if lock.locked():
                lock = None
                raise CredentialOperationError("PROFILE_BUSY")
            await lock.acquire()
            self._pending.add(profile_id)
            if ({**value, "scope": scope, "revision": expected_revision} == current
                    and self.applied_settings_revision(scope) == expected_revision):
                return {"settings": current, "applied_revision": expected_revision,
                        "monitor_status": self.account_monitor_status(scope)}
            drained = True
            await self._drain_account_reads(context)
            committing = True
            try:
                saved = await asyncio.to_thread(self.store.save_account_settings,
                                               {**value, "scope": scope}, expected_revision=expected_revision)
            except ValueError:
                committing = False
                raise
            context.account_reads_paused = False
            self._start_account_monitor(profile_id, context, saved)
            self._settings_revisions[profile_id] = saved["revision"]
            self.errors.pop(profile_id, None)
            return {"settings": saved, "applied_revision": self.applied_settings_revision(scope),
                    "monitor_status": self.account_monitor_status(scope)}
        except Exception as error:
            if drained:
                if not committing:
                    try:
                        latest = await asyncio.to_thread(self.store.load_account_settings, scope)
                        if latest != current:
                            raise RuntimeError("ACCOUNT_SETTINGS_CHANGED")
                        context.account_reads_paused = False
                        self._start_account_monitor(profile_id, context, current)
                    except Exception:
                        committing = True
                if committing:
                    await self._drain_account_reads(context, require_saved_events=False)
                    context.monitor_revision = None
                    self.errors[profile_id] = "ACCOUNT_SETTINGS_RECOVERY_REQUIRED"
                    raise CredentialOperationError("ACCOUNT_SETTINGS_RECOVERY_REQUIRED", 503) from None
            if isinstance(error, CredentialOperationError):
                raise
            code = str(error)
            if code not in {"ACCOUNT_SETTINGS_REVISION_CONFLICT", "ACCOUNT_IDENTITY_UNVERIFIED",
                            "ACCOUNT_SETTINGS_RECOVERY_REQUIRED", "ACCOUNT_SETTINGS_SCOPE_INVALID"}:
                code = "ACCOUNT_SETTINGS_APPLY_FAILED"
            raise CredentialOperationError(code, 404 if code == "ACCOUNT_IDENTITY_UNVERIFIED" else 409) from None
        finally:
            self._pending.discard(reservation)
            if lock is not None and lock.locked():
                self._pending.discard(profile_id)
                lock.release()

    @asynccontextmanager
    async def market_role_change(self, profile_id, *, expected_revision, expected_binding_revision):
        """Reserve a live role transaction; no transport or persisted role is changed here.

        Credential candidates remain reserved through READY/apply/cancel. Reject
        rather than waiting for them, since a user may keep a READY candidate open.
        The caller must keep drain/commit/publish inside this lexical boundary.
        """
        if self._closing or self._market_role_reservation is not None or self._pending:
            raise CredentialOperationError("PROFILE_BUSY")
        reservation = object()
        self._market_role_reservation = reservation
        self._market_role_idle.clear()
        try:
            plan = await self._check_market_role(profile_id, expected_revision, expected_binding_revision)
            if self._closing:
                raise CredentialOperationError("PROFILE_BUSY")
            self._market_role_reservation = plan
            yield plan
        finally:
            self._market_role_reservation = None
            self._market_role_idle.set()

    async def validate_market_role(self, plan):
        """Recheck the reserved snapshot immediately before a future role commit."""
        if self._closing or self._market_role_reservation is not plan:
            raise CredentialOperationError("PROFILE_BUSY")
        latest = await self._check_market_role(plan.target_profile_id, plan.expected_revision,
                                                plan.expected_binding_revision)
        if self._closing:
            raise CredentialOperationError("PROFILE_BUSY")
        if (latest != plan or latest.source is not plan.source or latest.target is not plan.target):
            raise CredentialOperationError("ACCOUNT_CONTEXT_MISMATCH")

    async def _check_market_role(self, profile_id, expected_revision, expected_binding_revision):
        import re
        if (not isinstance(profile_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", profile_id)
                or type(expected_revision) is not int or not 0 <= expected_revision <= 2**63 - 1
                or type(expected_binding_revision) is not int or not 1 <= expected_binding_revision <= 2**63 - 1):
            raise CredentialOperationError("MARKET_PROFILE_SETTINGS_INVALID")
        settings = await asyncio.to_thread(self.store.load_market_profile_settings)
        if settings["revision"] != expected_revision:
            raise CredentialOperationError("MARKET_PROFILE_SETTINGS_REVISION_CONFLICT")
        if settings["market_profile_id"] != self.market_profile_id:
            raise CredentialOperationError("MARKET_ROLE_RUNTIME_NOT_READY")
        source, target = self.bundle(self.market_profile_id), self.bundle(profile_id)
        if source is None or target is None:
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY")
        bindings = await asyncio.to_thread(self.store.load_account_bindings)
        profiles = {item["profile_id"] for item in await asyncio.to_thread(self.store.list_credential_profiles)
                    if (item["provider"], item["environment"], item["lifecycle_state"])
                    == ("kiwoom_real", "real", "active")}
        revisions = []
        for candidate_id, context in ((self.market_profile_id, source), (profile_id, target)):
            if candidate_id not in profiles:
                raise CredentialOperationError("MARKET_PROFILE_UNAVAILABLE")
            if context.binding is None:
                raise CredentialOperationError("ACCOUNT_IDENTITY_UNVERIFIED")
            stored = [item for item in bindings if item["credential_profile_id"] == candidate_id
                      and item["broker"] == "kiwoom" and item["environment"] == "real"]
            if (not stored or stored[-1]["account_ref"] != context.binding.scope.account_ref
                    or stored[-1]["binding_revision"] != context.binding.binding_revision):
                raise CredentialOperationError("ACCOUNT_CONTEXT_MISMATCH")
            configuration = await asyncio.to_thread(self.store.load_account_settings, context.binding.scope.to_dict())
            if configuration["active_profile_id"] != candidate_id:
                raise CredentialOperationError("MARKET_PROFILE_UNAVAILABLE")
            record = await asyncio.to_thread(self.vault.load, "kiwoom_real", candidate_id)
            if record is None or record.disabled or record.revision != self.active_revision(candidate_id):
                raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY")
            revisions.append(record.revision)
        if target.binding.binding_revision != expected_binding_revision:
            raise CredentialOperationError("ACCOUNT_CONTEXT_MISMATCH")
        return _MarketRolePlan(self.market_profile_id, profile_id, expected_revision,
                               expected_binding_revision, *revisions, source, target)

    async def prepare(self, profile_id, current, credentials, disabled):
        if self._market_role_recovery:
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
        if self._closing or self._market_role_reservation is not None or profile_id in self._pending:
            raise CredentialOperationError("PROFILE_BUSY")
        # Reserve before the first await, including disabled-role policy lookup.
        self._pending.add(profile_id)
        try:
            if disabled and profile_id in {self.market_profile_id, (await asyncio.to_thread(
                    self.store.load_market_profile_settings))["market_profile_id"]}:
                raise CredentialOperationError("MARKET_PROFILE_REQUIRED")
            return await self._prepare(profile_id, current, credentials, disabled)
        except BaseException:
            self._pending.discard(profile_id)
            raise

    async def _prepare(self, profile_id, current, credentials, disabled):
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient
        activation = current.payload.get("activation", {}) if current else {}
        if activation:
            await asyncio.to_thread(self.store.finalize_credential_activation, {
                **activation, "provider": "kiwoom_real", "profile_id": profile_id,
                "credential_revision": current.revision})
        context = self._contexts.get(profile_id)
        previous = self._bundles.get(profile_id)
        if disabled:
            account_ref = (context.binding.scope.account_ref if context and context.binding else
                           activation.get("account_ref"))
            run_id = context.run_id if context and context.run_id else activation.get("run_id")
            if not account_ref or not run_id:
                raise CredentialOperationError("ACCOUNT_IDENTITY_UNVERIFIED")
            prepared = identity = None
        else:
            settings = KiwoomSettings(credentials["app_key"], credentials["secret_key"], "real")
            async with self._slots:
                if context is not None:
                    source = context.broker
                    prepared = await (source.prepare_drained_credentials(settings) if source._credential_paused
                                      else source.prepare_credentials(settings))
                else:
                    async with self._probe_lock:
                        prepared = await self._probe.prepare_credentials(settings)
                        source = self._probe
                identity = KiwoomAccountIdentityReader(source, environment=AccountEnvironment.REAL,
                    hmac_key=self.hmac_key, now_provider=lambda: as_kst(source._client.server_now())
                ).identity_from_payload(prepared.account_payload)
                registered = await asyncio.to_thread(prepare_verified_account_identity, identity, self.store,
                    credential_profile_id=profile_id)
                account_ref = registered["account_ref"]
                bindings = await asyncio.to_thread(self.store.load_account_bindings)
                old = [binding for binding in bindings if binding["credential_profile_id"] == profile_id]
                if old and old[-1]["account_ref"] != account_ref:
                    raise CredentialOperationError("ACCOUNT_CHANGED")
                run_id = (context.run_id if context else None) or activation.get("run_id") or str(uuid.uuid4())
                if context is None:
                    client = source._client.fork_verified_candidate(prepared)
                    context = RealAccountContext(client, CentralRestBroker(client,
                        allowed_endpoints={**ACCOUNT_RECOVERY_ENDPOINTS, "ka00001": "/api/dostk/acnt", **{
                            key: "/api/dostk/acnt" for key in ("kt00007", "kt00015")}},
                        namespace=f"real-account:{account_ref}"))
        uuid.UUID(run_id)
        scope = AccountScope("kiwoom", AccountEnvironment.REAL, account_ref).to_dict()
        configuration = await asyncio.to_thread(self.store.load_account_settings, scope)
        if configuration["active_profile_id"] not in {None, profile_id}:
            raise CredentialOperationError("ACCOUNT_PROFILE_CONFLICT")
        lock = self._locks.setdefault(account_ref, asyncio.Lock())
        if lock.locked():
            raise CredentialOperationError("PROFILE_BUSY")
        await lock.acquire()
        if context is None:
            client = KiwoomRestClient(KiwoomSettings("", "", "real"))
            context = RealAccountContext(client, CentralRestBroker(client, namespace=f"real-account:{account_ref}"))
        plan = _RealCredentialPlan(context, prepared, identity, previous, account_ref, run_id,
            configuration["revision"], lock, self._revisions.get(profile_id), self._settings_revisions.get(profile_id))
        plan.previous_disabled = bool(current and current.disabled)
        return ValidatedCredential(account_ref=account_ref, run_id=run_id, prepared=plan)

    async def drain(self, profile_id, candidate):
        plan = candidate.prepared
        self._applying[profile_id] = plan
        self._contexts[profile_id] = plan.context  # Also own a transport on failed commit.
        self._bundles.pop(profile_id, None)
        self._revisions.pop(profile_id, None)
        self._settings_revisions.pop(profile_id, None)
        self._on_change(profile_id, None)
        await self._drain_account_reads(plan.context)
        if plan.context.account_queries is not None:
            await plan.context.account_queries.begin_credential_change()
        if profile_id == self.market_profile_id and self.collector is not None:
            await self.collector.begin_credential_change()
        await plan.context.broker.begin_credential_change()
        plan.drained = True
        configuration = await asyncio.to_thread(self.store.load_account_settings,
            AccountScope("kiwoom", AccountEnvironment.REAL, plan.account_ref).to_dict())
        if (configuration["revision"] != plan.settings_revision
                or configuration["active_profile_id"] not in {None, profile_id}):
            raise RuntimeError("ACCOUNT_SETTINGS_REVISION_CONFLICT")

    async def publish(self, profile_id, record, candidate):
        plan = candidate.prepared
        context = plan.context
        plan.committed = True
        scope = AccountScope("kiwoom", AccountEnvironment.REAL, plan.account_ref)
        if record.disabled:
            await asyncio.to_thread(context.client.disable_credentials)
            configuration = await asyncio.to_thread(self.store.load_account_settings, scope.to_dict())
            if configuration["active_profile_id"] is not None or configuration["monitor_enabled"]:
                raise RuntimeError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
            self._disabled_scopes[profile_id] = scope.to_dict()
            self._revisions[profile_id] = record.revision
            self._settings_revisions[profile_id] = configuration["revision"]
            self.errors.pop(profile_id, None)
            plan.published = True
            return
        try:
            await context.broker.activate_prepared_credentials(plan.prepared)
            bindings = await asyncio.to_thread(self.store.load_account_bindings)
            stored = [binding for binding in bindings if binding["credential_profile_id"] == profile_id][-1]
            context.binding = AccountBinding(profile_id, scope, stored["binding_revision"],
                datetime.fromisoformat(stored["verified_at"]), "ka00001")
            context.identity, context.run_id = plan.identity, plan.run_id
            configuration = await asyncio.to_thread(self.store.load_account_settings, scope.to_dict())
            if configuration["active_profile_id"] != profile_id:
                raise RuntimeError("ACCOUNT_PROFILE_CONFLICT")
            await context.broker.end_credential_change()
            if context.account_queries is None:
                context.account_queries = AccountQuerySessionManager(context.broker, lambda: context.binding,
                    read_limiter=None if profile_id == self.market_profile_id else self._slots)
            elif context.account_queries._credential_paused:
                context.account_queries.end_credential_change(invalidate_cursors=True)
            if self._closing:
                raise RuntimeError("REAL_OWNER_CLOSED")
            # REST/binding is committed before the first new account REAL frame.
            self._bundles[profile_id] = context
            context.account_reads_paused = False
            self._start_account_monitor(profile_id, context, configuration)
            self._on_change(profile_id, context)
            if profile_id == self.market_profile_id and self.collector is not None:
                await self.collector.end_credential_change()
            if profile_id == self.market_profile_id:
                self._market_role_revision = (await asyncio.to_thread(
                    self.store.load_market_profile_settings))["revision"]
            self._revisions[profile_id] = record.revision
            self._settings_revisions[profile_id] = configuration["revision"]
            self._disabled_scopes.pop(profile_id, None)
            self.errors.pop(profile_id, None)
            plan.published = True
            self._on_change(profile_id, context)
        except BaseException:
            self._bundles.pop(profile_id, None)
            await self._drain_account_reads(context, require_saved_events=False)
            self._on_change(profile_id, None)
            if context.account_queries is not None:
                await context.account_queries.begin_credential_change()
            if profile_id == self.market_profile_id and self.collector is not None:
                await self.collector.begin_credential_change()
            await context.broker.begin_credential_change()
            raise

    async def resume(self, profile_id):
        plan = self._applying.get(profile_id)
        if plan is None or plan.published:
            return
        if plan.committed:
            raise RuntimeError("ACTIVATION_RECOVERY_REQUIRED")
        context = plan.context
        if plan.previous_disabled and plan.previous_revision is not None:
            self._revisions[profile_id] = plan.previous_revision
            self._settings_revisions[profile_id] = plan.previous_settings_revision
            plan.resumed = True
            return  # Tombstone stays accepting no queries and retaining no active token.
        if plan.previous is None:
            # Keyless/previously failed contexts remain fenced on precommit abort.
            plan.resumed = True
            return
        if context.broker._credential_paused:
            await context.broker.end_credential_change()
        if context.account_queries is not None and context.account_queries._credential_paused:
            context.account_queries.end_credential_change(invalidate_cursors=False)
        self._bundles[profile_id] = context
        context.account_reads_paused = False
        try:
            await self._restart_account_monitors(profile_id)
            self._on_change(profile_id, context)
            if profile_id == self.market_profile_id and self.collector is not None:
                await self.collector.end_credential_change()
        except BaseException:
            self._bundles.pop(profile_id, None)
            await self._drain_account_reads(context, require_saved_events=False)
            self._on_change(profile_id, None)
            raise
        self._revisions[profile_id] = plan.previous_revision
        self._settings_revisions[profile_id] = plan.previous_settings_revision
        plan.resumed = True

    def release(self, profile_id, candidate):
        plan = candidate.prepared
        self._pending.discard(profile_id)
        if self._applying.get(profile_id) is plan:
            self._applying.pop(profile_id)
            if not plan.published and not plan.resumed:
                self.errors[profile_id] = "ACTIVATION_RECOVERY_REQUIRED"
        if plan.lock is not None and plan.lock.locked():
            plan.lock.release()
        plan.lock = plan.identity = plan.prepared = None

    async def start(self):
        if self._closing:
            raise RuntimeError("REAL_OWNER_CLOSED")
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start(), name="real-owner-start")
        await asyncio.shield(self._start_task)

    async def _start(self):
        market = self._contexts.get(self.market_profile_id)
        if market is not None:
            if self.collector is not None:
                await self.collector.begin_credential_change()
            await market.broker.begin_credential_change()
        for profile in await asyncio.to_thread(self.store.list_credential_profiles):
            if profile["provider"] != "kiwoom_real" or profile["lifecycle_state"] != "active":
                continue
            profile_id = profile["profile_id"]
            if profile_id == self.market_profile_id:
                await self._boot(profile_id)
            else:
                self._boot_tasks.append(asyncio.create_task(self._boot(profile_id), name="real-profile-bootstrap"))

    async def _boot(self, profile_id):
        candidate = None
        try:
            record = await asyncio.to_thread(self.vault.load, "kiwoom_real", profile_id)
            if record is None or profile_id in self._pending or self.active_revision(profile_id) is not None:
                return
            candidate = await self.prepare(profile_id, record, record.payload["credentials"], record.disabled)
            plan = candidate.prepared
            if not record.disabled and not record.payload.get("activation"):
                await asyncio.to_thread(bind_verified_account_identity, plan.identity, self.store,
                                        credential_profile_id=profile_id)
                configuration = await asyncio.to_thread(self.store.load_account_settings,
                    AccountScope("kiwoom", AccountEnvironment.REAL, plan.account_ref).to_dict())
                if configuration["revision"] == 0:
                    await asyncio.to_thread(self.store.save_account_settings, {
                        "scope": configuration["scope"], "active_profile_id": profile_id,
                        "monitor_enabled": True, "mock_order_enabled": False}, expected_revision=0)
                    plan.settings_revision = 1
            await self.drain(profile_id, candidate)
            await self.publish(profile_id, record, candidate)
        except Exception:
            self.errors[profile_id] = "REAL_ACCOUNT_STARTUP_FAILED"
            self._on_change(profile_id, None)
        finally:
            if candidate is not None:
                self.release(profile_id, candidate)

    async def close(self):
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="real-owner-close")
        await asyncio.shield(self._close_task)

    async def _close(self):
        await asyncio.gather(*tuple(self._settings_tasks), return_exceptions=True)
        await asyncio.gather(*tuple(self._role_tasks), return_exceptions=True)
        await self._market_role_idle.wait()
        if self._start_task is not None:
            await asyncio.gather(asyncio.shield(self._start_task), return_exceptions=True)
        await asyncio.gather(*self._boot_tasks, return_exceptions=True)
        for context in self._contexts.values():
            await self._drain_account_reads(context)
            if context.account_queries is not None:
                await context.account_queries.close()
            # The shared market broker/collector are closed by the server after producers.
            if context.broker is not self._market_broker:
                await context.broker.close()
        await self._probe.close()
        self._bundles.clear()
        self._revisions.clear()
