"""Single-owner NAS boundary for broker-backed mock execution."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Awaitable, Callable, Iterator

from kiwoom_monitor.application.market_session_schedule import mock_order_entry_decision
from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
)
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord, ExecutionRepository


class ExecutionRuntime:
    def __init__(
        self, lifecycle: OrderLifecycle, repository: ExecutionRepository,
        *, account_ref: str, run_id: str, owner_token: str,
    ) -> None:
        if not account_ref.strip() or not run_id.strip() or not owner_token.strip():
            raise ValueError("account_ref, run_id and owner_token are required")
        self._lifecycle = lifecycle
        self._repository = repository
        self.account_ref = account_ref
        self.run_id = run_id
        self.owner_token = owner_token
        self._active = False
        self._lease_seconds = 60
        self._operation_lock = RLock()
        self._state_lock = RLock()
        self._new_orders_enabled = True  # Preserve the existing explicitly enabled manual transport.
        self._retired = False

    def start(
        self, *, lease_seconds: int = 60, new_orders_enabled: bool | None = None,
    ) -> None:
        with self._operation_lock, self._state_lock:
            if self._retired:
                raise RuntimeError("MOCK_RUNTIME_RETIRED")
            if new_orders_enabled is not None:
                self._new_orders_enabled = bool(new_orders_enabled)
            now = datetime.now(timezone.utc)
            self._lease_seconds = max(1, lease_seconds)
            self._active = self._repository.claim_runtime(
                "mock", self.account_ref, self.run_id, self.owner_token, now, self._lease_seconds,
            )
            if not self._active:
                raise RuntimeError("another mock execution runtime owns this account and run")
            try:
                self._repository.bind_runtime_owner(self.account_ref, self.run_id, self.owner_token)
            except Exception:
                self._active = False
                self._repository.release_runtime("mock", self.account_ref, self.run_id, self.owner_token)
                raise

    def set_new_orders_enabled(self, enabled: bool) -> None:
        with self._operation_lock:
            if self._retired:
                raise RuntimeError("MOCK_RUNTIME_RETIRED")
            self._new_orders_enabled = bool(enabled)

    def stop(self) -> bool:
        # The bundle owner must drain gateway/monitor work before releasing its lease.
        with self._operation_lock, self._state_lock:
            self._active, self._new_orders_enabled, self._retired = False, False, True
            return self._repository.release_runtime("mock", self.account_ref, self.run_id, self.owner_token)

    def heartbeat(self) -> None:
        # A slow order must not prevent lease renewal while its network call finishes.
        with self._state_lock:
            if not self._active:
                raise RuntimeError("mock execution runtime has not acquired ownership")
            if not self._repository.claim_runtime(
                "mock", self.account_ref, self.run_id, self.owner_token,
                datetime.now(timezone.utc), self._lease_seconds,
            ):
                self._active = False
                raise RuntimeError("mock execution ownership was lost")

    def submit(
        self, intent: OrderIntent, account: AccountSnapshot, *, reference_price: int | None = None,
    ) -> ExecutionRecord:
        with self._operation_lock:
            self._require_active(intent)
            if not self._new_orders_enabled:
                raise RuntimeError("MOCK_NEW_ORDERS_DISABLED")
            self._repository.save_account_snapshot("mock", account, datetime.now(timezone.utc))
            self._lifecycle.queue(intent)
            return self._lifecycle.submit(intent.intent_id, account, reference_price=reference_price)

    def load_intent(self, intent_id: str) -> ExecutionRecord | None:
        """Read a deterministic intent before deciding whether a retry may submit."""
        return self._repository.load(intent_id)

    @contextmanager
    def automation_decision_guard(self) -> Iterator[None]:
        """Serialize one safety check and submission while keeping manual entry closed."""
        with self._operation_lock:
            self.heartbeat()
            if self._new_orders_enabled:
                raise RuntimeError("MOCK_AUTOMATION_REQUIRES_CLOSED_ORDER_GATE")
            try:
                yield
            finally:
                self._new_orders_enabled = False

    def submit_automation_intent(
        self, intent: OrderIntent, account: AccountSnapshot, *, reference_price: int | None = None,
    ) -> ExecutionRecord:
        """Submit one approved automation intent without leaving the account gate open."""
        with self._operation_lock:
            self._require_active(intent)
            if self._new_orders_enabled:
                raise RuntimeError("MOCK_AUTOMATION_REQUIRES_CLOSED_ORDER_GATE")
            self._new_orders_enabled = True
            try:
                self._repository.save_account_snapshot("mock", account, datetime.now(timezone.utc))
                self._lifecycle.queue(intent)
                return self._lifecycle.submit(
                    intent.intent_id, account, reference_price=reference_price,
                )
            finally:
                self._new_orders_enabled = False

    def reconcile(self, intent_id: str, snapshot: BrokerOrderSnapshot) -> ExecutionRecord:
        with self._operation_lock:
            record = self._repository.load(intent_id)
            if record is None:
                raise KeyError(f"unknown order intent: {intent_id}")
            self._require_active(record.intent)
            return self._lifecycle.reconcile(intent_id, snapshot)

    def cancel(self, intent_id: str, quantity: int = 0) -> ExecutionRecord:
        with self._operation_lock:
            record = self._repository.load(intent_id)
            if record is None:
                raise KeyError(f"unknown order intent: {intent_id}")
            self._require_active(record.intent)
            return self._lifecycle.cancel(intent_id, quantity)

    def _require_active(self, intent: OrderIntent) -> None:
        with self._state_lock:
            if not self._active:
                raise RuntimeError("mock execution runtime has not acquired ownership")
            if intent.environment != "mock" or intent.account_ref != self.account_ref or intent.run_id != self.run_id:
                raise ValueError("intent does not belong to this mock runtime")
            if not self._repository.claim_runtime(
                "mock", self.account_ref, self.run_id, self.owner_token,
                datetime.now(timezone.utc), self._lease_seconds,
            ):
                self._active = False
                raise RuntimeError("mock execution ownership was lost")


class ManualMockOrderGateway:
    """Authenticated manual command boundary; it never generates orders on its own."""

    def __init__(
        self,
        runtime: ExecutionRuntime,
        repository: ExecutionRepository,
        account_refresh: Callable[[], Awaitable[AccountSnapshot]],
        *,
        now_provider: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._runtime = runtime
        self._repository = repository
        self._account_refresh = account_refresh
        self._now = now_provider
        self._command_lock = asyncio.Lock()
        self._accepting = True
        self._commands: set[asyncio.Task[ExecutionRecord]] = set()
        self._drain_task: asyncio.Task[None] | None = None
        self._closed = False

    async def begin_credential_change(self) -> None:
        self._accepting = False
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain_commands(), name="mock-command-drain")
        await asyncio.shield(self._drain_task)

    async def _drain_commands(self) -> None:
        if self._commands:
            await asyncio.gather(*(asyncio.shield(task) for task in tuple(self._commands)), return_exceptions=True)

    def end_credential_change(self) -> None:
        if self._closed:
            raise RuntimeError("MOCK_GATEWAY_CLOSED")
        if self._drain_task is None or not self._drain_task.done():
            raise RuntimeError("MOCK_COMMAND_DRAIN_NOT_COMPLETE")
        self._drain_task.result()
        self._drain_task = None
        self._accepting = True

    async def close(self) -> None:
        self._closed = True
        await self.begin_credential_change()

    async def _command(self, factory: Callable[[], Awaitable[ExecutionRecord]]) -> ExecutionRecord:
        if not self._accepting:
            raise RuntimeError("MOCK_CREDENTIAL_CHANGE_IN_PROGRESS")
        task = asyncio.create_task(factory(), name="mock-manual-command")
        self._commands.add(task)
        def completed(done: asyncio.Task[ExecutionRecord]) -> None:
            self._commands.discard(done)
            if not done.cancelled(): done.exception()
        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def submit_limit(
        self,
        *,
        request_id: str,
        symbol: str,
        side: OrderSide,
        quantity: int,
        limit_price: int,
        expires_seconds: int,
        scoped: bool = False,
    ) -> ExecutionRecord:
        return await self._command(lambda: self._submit_limit(request_id=request_id, symbol=symbol, side=side,
            quantity=quantity, limit_price=limit_price, expires_seconds=expires_seconds, scoped=scoped))

    async def _submit_limit(
        self, *, request_id: str, symbol: str, side: OrderSide, quantity: int,
        limit_price: int, expires_seconds: int,
        scoped: bool = False,
    ) -> ExecutionRecord:
        intent_id = self.intent_id(request_id, scoped=scoped)
        async with self._command_lock:
            existing = await asyncio.to_thread(self._repository.load, intent_id)
            if existing is not None:
                self.load(intent_id)
                self._verify_same_request(existing, symbol, side, quantity, limit_price)
                return existing
            account = await self._account_refresh()
            created_at = self._aware_now()
            order_policy = mock_order_entry_decision(
                created_at, environment="mock", venue="KRX", order_type=OrderType.LIMIT.value,
            )
            intent = OrderIntent(
                intent_id=intent_id,
                run_id=self._runtime.run_id,
                decision_id=f"manual:{request_id.strip()}",
                account_ref=self._runtime.account_ref,
                environment="mock",
                symbol=symbol,
                venue="KRX",
                side=side,
                quantity=quantity,
                order_type=OrderType.LIMIT,
                limit_price=limit_price,
                created_at=created_at,
                expires_at=created_at + timedelta(seconds=expires_seconds),
                policy_version=order_policy.policy_version,
            )
            return await asyncio.to_thread(self._runtime.submit, intent, account)

    async def cancel(self, intent_id: str, quantity: int = 0) -> ExecutionRecord:
        return await self._command(lambda: self._cancel(intent_id, quantity))

    async def _cancel(self, intent_id: str, quantity: int) -> ExecutionRecord:
        async with self._command_lock:
            await asyncio.to_thread(self.load, intent_id)
            # A fresh broker snapshot prevents a filled order from being cancelled
            # based only on an older local state.
            await self._account_refresh()
            return await asyncio.to_thread(self._runtime.cancel, intent_id, quantity)

    def load(self, intent_id: str) -> ExecutionRecord:
        record = self._repository.load(intent_id)
        if record is None:
            raise KeyError(f"unknown order intent: {intent_id}")
        if (
            record.intent.environment != "mock"
            or record.intent.account_ref != self._runtime.account_ref
            or record.intent.run_id != self._runtime.run_id
        ):
            raise KeyError(f"unknown order intent: {intent_id}")
        return record

    def events(self, intent_id: str) -> tuple[dict[str, object], ...]:
        self.load(intent_id)
        return self._repository.events(intent_id)

    def intent_id(self, request_id: str, *, scoped: bool = False) -> str:
        normalized = request_id.strip()
        if not normalized:
            raise ValueError("request_id is required")
        if scoped:
            fingerprint = f"mock\0{self._runtime.account_ref}\0{self._runtime.run_id}\0{normalized}".encode("utf-8")
            return f"manual_mock_scoped_{hashlib.sha256(fingerprint).hexdigest()[:32]}"
        fingerprint = f"{self._runtime.run_id}\0{normalized}".encode("utf-8")
        return f"manual_mock_{hashlib.sha256(fingerprint).hexdigest()[:32]}"

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("manual mock order clock must be timezone-aware")
        return value

    @staticmethod
    def _verify_same_request(
        record: ExecutionRecord,
        symbol: str,
        side: OrderSide,
        quantity: int,
        limit_price: int,
    ) -> None:
        intent = record.intent
        if (
            intent.symbol != symbol
            or intent.side is not side
            or intent.quantity != quantity
            or intent.limit_price != limit_price
            or intent.order_type is not OrderType.LIMIT
        ):
            raise ValueError("request_id already belongs to a different mock order")
