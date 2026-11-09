"""Single-owner NAS boundary for broker-backed mock execution."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterator, Mapping

from kiwoom_monitor.application.market_session_schedule import mock_order_entry_decision
from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.domain.execution_activation import CloseExitCalendarRecheck
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    OrderIntent,
    OrderPlanRef,
    OrderSide,
    OrderState,
    OrderType,
    PlannedOrderIntent,
    TERMINAL_ORDER_STATES,
)
from kiwoom_monitor.infrastructure.persistence.execution_repository import (
    ExecutionRecord, ExecutionRepository, execution_broker_legs, execution_broker_venue,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from .market_observations import KST, as_kst

if TYPE_CHECKING:
    from kiwoom_monitor.application.close_mock_execution import CloseEntryDeadlineCancelClaim
    from kiwoom_monitor.application.mock_automation_risk import (
        PlannedLeaderCancelClaim, PlannedLeaderExpiredEntryCancelClaim, PlannedLeaderStopCancelRequest,
    )


class AutomationViBuyBlocked(ValueError):
    """A buy needs to be reconsidered after the latest VI observation."""


class ExecutionRuntime:
    def __init__(
        self, lifecycle: OrderLifecycle, repository: ExecutionRepository,
        *, account_ref: str, run_id: str, owner_token: str,
        now_provider: Callable[[], datetime] = lambda: datetime.now(KST),
        buy_vi_rejection: Callable[[str, datetime], str | None] | None = None,
        rehearsal_lease_now: Callable[[], datetime] | None = None,
    ) -> None:
        if not account_ref.strip() or not run_id.strip() or not owner_token.strip():
            raise ValueError("account_ref, run_id and owner_token are required")
        self._lifecycle = lifecycle
        self._repository = repository
        self._environment = 'real' if getattr(repository, 'local_order_rehearsal', False) is True else 'mock'
        if rehearsal_lease_now is not None and self._environment != 'real':
            raise ValueError('separate lease clock is restricted to local rehearsal')
        self._lease_now = rehearsal_lease_now or self.server_now
        self.account_ref = account_ref
        self.run_id = run_id
        self.owner_token = owner_token
        self._now = now_provider
        self._buy_vi_rejection = buy_vi_rejection
        self._active = False
        self._lease_seconds = 60
        self._operation_lock = RLock()
        self._state_lock = RLock()
        self._new_orders_enabled = True  # Preserve the existing explicitly enabled manual transport.
        self._retired = False
        self._observation_revision = 0
        self._observation_operations = 0
        self._observation_lock = RLock()
        self._observation_listener = None

    @property
    def observation_revision(self) -> int:
        with self._observation_lock:
            return self._observation_revision

    @property
    def observation_in_progress(self) -> bool:
        with self._observation_lock:
            return self._observation_operations > 0

    @contextmanager
    def _observe_operation(self):
        with self._observation_lock:
            self._observation_operations += 1
        self._order_observation_changed()
        try:
            yield
        finally:
            with self._observation_lock:
                self._observation_operations -= 1
            self._order_observation_changed()

    def set_observation_listener(self, listener) -> None:
        with self._observation_lock:
            self._observation_listener = listener

    def _order_observation_changed(self) -> None:
        with self._observation_lock:
            self._observation_revision += 1
            listener = self._observation_listener
        if listener is not None:
            try:
                listener()
            except Exception:
                logging.getLogger(__name__).exception("계좌 RAM 관측 무효화 알림 실패")

    def start(
        self, *, lease_seconds: int = 60, new_orders_enabled: bool | None = None,
    ) -> None:
        with self._operation_lock, self._state_lock:
            if self._retired:
                raise RuntimeError("MOCK_RUNTIME_RETIRED")
            if new_orders_enabled is not None:
                self._new_orders_enabled = bool(new_orders_enabled)
            now = self._lease_now()
            self._lease_seconds = max(1, lease_seconds)
            self._active = self._repository.claim_runtime(
                self._environment, self.account_ref, self.run_id, self.owner_token, now, self._lease_seconds,
            )
            if not self._active:
                raise RuntimeError("another mock execution runtime owns this account and run")
            try:
                self._repository.bind_runtime_owner(self.account_ref, self.run_id, self.owner_token)
            except Exception:
                self._active = False
                self._repository.release_runtime(self._environment, self.account_ref, self.run_id, self.owner_token)
                raise

    def set_new_orders_enabled(self, enabled: bool) -> None:
        with self._operation_lock:
            if self._retired:
                raise RuntimeError("MOCK_RUNTIME_RETIRED")
            self._new_orders_enabled = bool(enabled)

    def stop(self) -> bool:
        # The bundle owner must drain gateway/monitor work before releasing its lease.
        with self._operation_lock, self._state_lock, self._observe_operation():
            self._active, self._new_orders_enabled, self._retired = False, False, True
            return self._repository.release_runtime(self._environment, self.account_ref, self.run_id, self.owner_token)

    def heartbeat(self) -> None:
        # A slow order must not prevent lease renewal while its network call finishes.
        with self._state_lock:
            if not self._active:
                raise RuntimeError("mock execution runtime has not acquired ownership")
            if not self._repository.claim_runtime(
                self._environment, self.account_ref, self.run_id, self.owner_token,
                self._lease_now(), self._lease_seconds,
            ):
                self._active = False
                raise RuntimeError("mock execution ownership was lost")

    def submit(
        self, intent: OrderIntent, account: AccountSnapshot, *, reference_price: int | None = None,
    ) -> ExecutionRecord:
        with self._operation_lock, self._observe_operation():
            if isinstance(intent, PlannedOrderIntent):
                raise ValueError("planned orders require the automation approval path")
            self._require_active(intent)
            if not self._new_orders_enabled:
                raise RuntimeError("MOCK_NEW_ORDERS_DISABLED")
            self._repository.save_account_snapshot(self._environment, account, self.server_now())
            self._lifecycle.queue(intent)
            return self._lifecycle.submit(intent.intent_id, account, reference_price=reference_price)

    def load_intent(self, intent_id: str) -> ExecutionRecord | None:
        """Read a deterministic intent before deciding whether a retry may submit."""
        return self._repository.load(intent_id)

    def active_intent_reader_for(
        self, repository: object,
    ) -> Callable[..., tuple[ExecutionRecord, ...]] | None:
        """Offer the runtime's own run-scoped reader for a shared native scope."""
        shares_reader = getattr(type(repository), "shares_active_intent_reader", None)
        if (type(self._repository) is ExecutionRepository
                and callable(shares_reader) and shares_reader(repository, self._repository)):
            return self.load_active_intents
        return None

    def load_active_intents(self, *, _reader: Any = None) -> tuple[ExecutionRecord, ...]:
        reader_args = {} if _reader is None else {"_reader": _reader}
        return self._repository.active_intents(self._environment, self.account_ref, self.run_id, **reader_args)

    def load_account_active_intents(self):
        return self._repository.account_active_intents(self.account_ref)

    def load_intent_events(self, intent_id: str):
        return self._repository.events(intent_id)

    def load_intent_claim_approval(self, intent_id: str):
        return self._repository.claim_approval(intent_id)

    def server_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("execution runtime clock must be timezone-aware")
        return as_kst(value)

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
        spec_id: str, control_revision: int, replacement_claim: dict | None = None,
        protection_claim: dict | None = None,
        plan_recovery: MockAccountRecovery | None = None,
    ) -> ExecutionRecord:
        """Submit one approved automation intent without leaving the account gate open."""
        if self._environment != "mock":
            raise ValueError("production mock plans cannot enter local rehearsal")
        with self._operation_lock, self._observe_operation():
            if isinstance(intent, PlannedOrderIntent):
                if replacement_claim is not None:
                    raise ValueError("planned orders require their own exact approval")
                return self._submit_planned(intent, account, spec_id, control_revision, plan_recovery,
                                            protection_claim=protection_claim)
            if plan_recovery is not None:
                raise ValueError("plan recovery requires a planned order")
            self._require_active(intent)
            if self._new_orders_enabled:
                raise RuntimeError("MOCK_AUTOMATION_REQUIRES_CLOSED_ORDER_GATE")
            vi_precheck = self._automation_vi_precheck(intent)
            self._repository.bind_automation_control(
                spec_id=spec_id, control_revision=control_revision,
                replacement_claim=replacement_claim,
                **({'protection_claim':protection_claim} if protection_claim is not None else {}),
            )
            self._new_orders_enabled = True
            try:
                try:
                    self._repository.save_account_snapshot(self._environment, account, self.server_now())
                    self._lifecycle.queue(intent)
                finally:
                    # The control revision is consumed by the durable QUEUED claim.
                    # Broker submission/reconciliation must continue when a later STOP arrives.
                    self._repository.clear_automation_control()
                return self._lifecycle.submit(
                    intent.intent_id, account, reference_price=reference_price,
                    **({"before_submission_claim": vi_precheck} if vi_precheck is not None else {}),
                )
            finally:
                self._new_orders_enabled = False

    def _submit_planned(
        self, intent: PlannedOrderIntent, account: AccountSnapshot, spec_id: str, control_revision: int,
        recovery: MockAccountRecovery | None, *, protection_claim: dict | None = None,
    ) -> ExecutionRecord:
        self._require_active(intent)
        if self._new_orders_enabled:
            raise RuntimeError("MOCK_AUTOMATION_REQUIRES_CLOSED_ORDER_GATE")
        existing = self._repository.load(intent.intent_id)
        if existing is not None:
            if existing.intent != intent:
                raise ValueError("intent_id already belongs to a different order")
            if existing.state is OrderState.QUEUED and self.server_now() >= intent.expires_at:
                return self._lifecycle.release_queued_plan(existing, expired=True)
            return existing
        if recovery is None or recovery.account != account:
            raise ValueError("planned order requires the exact broker recovery snapshot")
        vi_precheck = self._automation_vi_precheck(intent)
        self._repository.bind_automation_control(
            spec_id=spec_id, control_revision=control_revision, plan_recovery=recovery,
            **({"protection_claim": protection_claim} if protection_claim is not None else {}),
        )
        try:
            record, first_claim = self._lifecycle.queue_planned(intent)
            if not first_claim:
                return record
            try:
                return self._lifecycle.submit(
                    intent.intent_id, account,
                    after_submission_claim=self._repository.clear_automation_control,
                    **({"before_submission_claim": vi_precheck} if vi_precheck is not None else {}),
                )
            except CloseExitCalendarRecheck:
                self._lifecycle.release_queued_plan(record)
                raise
        finally:
            self._repository.clear_automation_control()

    def check_automation_buy_vi(self, symbol: str, observed_at: datetime) -> None:
        if self._buy_vi_rejection is not None and (reason := self._buy_vi_rejection(symbol, observed_at)):
            raise AutomationViBuyBlocked(reason)

    def _automation_vi_precheck(self, intent: OrderIntent) -> Callable[[], str | None] | None:
        source = self._buy_vi_rejection
        if source is None or intent.side is not OrderSide.BUY:
            return None
        self.check_automation_buy_vi(intent.symbol, intent.created_at)
        return lambda: source(intent.symbol, intent.created_at)

    def reconcile(self, intent_id: str, snapshot: BrokerOrderSnapshot, *, broker_venue=None) -> ExecutionRecord:
        with self._operation_lock, self._observe_operation():
            record = self._repository.load(intent_id)
            if record is None:
                raise KeyError(f"unknown order intent: {intent_id}")
            self._require_active(record.intent)
            if not self._matches_broker_venue(record, broker_venue):
                raise ValueError('broker snapshot did not confirm the original venue')
            return self._lifecycle.reconcile(intent_id, snapshot,
                **({'broker_venue': broker_venue} if record.intent.venue == 'SOR' else {}))

    def reconcile_by_broker_order(self, snapshot: BrokerOrderSnapshot, *, broker_venue=None,
                                  broker_details=()) -> ExecutionRecord | None:
        scope = (self._repository.broker_reconciliation_scope()
                 if type(self._repository) is ExecutionRepository else nullcontext())
        with scope as connection:
            connection_args = {} if connection is None else {"_connection": connection}
            record = self._repository.find_by_broker_order_id(
                self._environment, self.account_ref, self.run_id, snapshot.broker_order_id, **connection_args,
            )
            if (record is None or record.intent.run_id != self.run_id
                    or not self._matches_broker_venue(record, broker_venue, broker_details)):
                return None
            with self._operation_lock, self._observe_operation():
                self._require_active(record.intent, **connection_args)
                # Start a fresh read after the lease commit, even on the same session.
                return self._lifecycle.reconcile(record.intent.intent_id, snapshot, **connection_args,
                    **({'broker_venue': broker_venue} if record.intent.venue == 'SOR' else {}))

    def recover_amendments(self, recovery: MockAccountRecovery, records=None) -> int:
        """Bind broker facts/replay retained fills under the same run owner; never transmit."""
        if recovery.account.account_ref != self.account_ref:
            raise ValueError("amendment recovery account mismatch")
        active = records if records is not None else self._repository.active_intents(
            self._environment, self.account_ref, self.run_id)
        selected = [record for record in active if record.intent.run_id == self.run_id
                    and record.amendment is not None and record.amendment.reconciliation_pending]
        if not selected:
            return 0  # Ordinary recovery must not wait on an unrelated in-flight order command.
        with self._operation_lock:
            before = self.observation_revision
            for record in selected:
                self._require_active(record.intent)
                with self._observe_operation():
                    self._lifecycle.recover_amendment(record.intent.intent_id, recovery)
            return self.observation_revision - before

    @staticmethod
    def _matches_broker_venue(record, broker_venue, broker_details=()):
        intent = record.intent
        if intent.venue == 'SOR':
            if broker_venue not in {'KRX', 'NXT'}:
                return False
            stored = execution_broker_venue(record)
            if stored is not None:
                return broker_venue == stored
            return bool(record.broker_order_id and broker_details) and all(
                (item.broker_order_id, item.venue, item.side, item.ordered_quantity, item.limit_price)
                == (record.broker_order_id, broker_venue, intent.side, intent.quantity, intent.limit_price)
                and item.venue_confirmed and not item.original_broker_order_id and not item.reasons
                for item in broker_details)
        if intent.environment != 'mock':
            return True  # LocalOrderRehearsal already owns its broker scope.
        return (broker_venue in {None, 'KRX'} if intent.venue == 'KRX' else broker_venue == intent.venue)

    def observe_amendment_fill(self, snapshot, *, original_broker_order_id, side, broker_venue=None):
        with self._operation_lock:
            if not original_broker_order_id:
                return None
            record = self._repository.find_by_broker_order_id(
                self._environment, self.account_ref, self.run_id, original_broker_order_id)
            if (record is None or record.amendment is None or not record.amendment.unresolved
                    or not self._matches_broker_venue(record, broker_venue)):
                return None
            self._require_active(record.intent)
            with self._observe_operation():
                return self._lifecycle.observe_amendment_fill(record.intent.intent_id, snapshot,
                    original_broker_order_id=original_broker_order_id, side=side)

    def cancel(self, intent_id: str, quantity: int = 0) -> ExecutionRecord:
        with self._operation_lock, self._observe_operation():
            record = self._repository.load(intent_id)
            if record is None:
                raise KeyError(f"unknown order intent: {intent_id}")
            self._require_active(record.intent)
            return self._lifecycle.cancel(intent_id, quantity)

    def manage_rehearsal_limit(self, intent_id: str, decide) -> ExecutionRecord:
        """Reassess a single confirmed working leg under the existing order owner."""
        if self._environment != 'real':
            raise ValueError('local rehearsal runtime required')
        with self.automation_decision_guard(), self._observe_operation():
            record = self._repository.load(intent_id)
            if record is None:
                raise KeyError(f'unknown order intent: {intent_id}')
            self._require_active(record.intent)
            if record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
                return record
            active = [leg for leg in execution_broker_legs(record)
                      if leg.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED} and leg.remaining_quantity]
            if len(active) != 1:
                return record
            leg = active[0]
            valid, price = decide(record, leg)
            if valid is False:
                return self._lifecycle.cancel(intent_id, broker_order_id=leg.broker_order_id)
            if valid is not True or price is None or price == leg.limit_price:
                return record
            previous = record.amendment
            if (previous is not None and previous.status == 'REJECTED'
                    and (previous.broker_order_id, previous.limit_price) == (leg.broker_order_id, price)):
                return record
            return self._lifecycle.amend(intent_id, leg.remaining_quantity, price, broker_order_id=leg.broker_order_id)

    def release_unsent_plan(self, expected):
        with self._operation_lock, self._observe_operation():
            self._require_active(expected.intent)
            current = self._repository.load(expected.intent.intent_id)
            if current == expected and current.state is OrderState.QUEUED:
                return self._lifecycle.release_queued_plan(current)
            return current

    def manage_close_order(self, expected, *, spec_id, control_revision, build_claim):
        """One current-condition claim on the existing owned order, never a replacement entry."""
        with self._operation_lock:
            self._require_active(expected.intent)
            if self._new_orders_enabled:
                raise RuntimeError('MOCK_AUTOMATION_REQUIRES_CLOSED_ORDER_GATE')
            record = self._repository.load(expected.intent.intent_id)
            if record is None or record.intent != expected.intent:
                raise ValueError('CLOSE_CURRENT_ORDER_ORIGIN_CHANGED')
            if record.state in TERMINAL_ORDER_STATES or record.state is OrderState.SUBMISSION_UNKNOWN:
                return record
            claim = build_claim(record)
            if claim is None or claim.assessment['action'] == 'WAIT':
                return record
            assessment = claim.to_dict()
            purpose = 'amendment_claim' if assessment['action'] == 'AMEND' else 'planned_cancel_claim'
            self._repository.bind_automation_control(spec_id=spec_id, control_revision=control_revision,
                plan_recovery=claim.recovery, **{purpose: assessment})
            try:
                with self._observe_operation():
                    if assessment['action'] == 'AMEND':
                        vi_check = {}
                        if self._buy_vi_rejection is not None and record.intent.side is OrderSide.BUY:
                            quote_at = datetime.fromisoformat(assessment['quote']['available_at'])
                            vi_check["before_amendment_claim"] = lambda: self.check_automation_buy_vi(record.intent.symbol, quote_at)
                        return self._lifecycle.amend(record.intent.intent_id, assessment['quantity'], assessment['limit_price'],
                            broker_order_id=assessment['broker_order_id'], assessment=assessment,
                            after_amendment_claim=self._repository.clear_automation_control, **vi_check)
                    return self._lifecycle.cancel(record.intent.intent_id, broker_order_id=assessment['broker_order_id'],
                        after_cancel_claim=self._repository.clear_automation_control)
            finally:
                self._repository.clear_automation_control()
                self._new_orders_enabled = False

    def cancel_automation_intent(
        self, expected: ExecutionRecord, *, spec_id: str, control_revision: int,
        before_cancel_claim: Callable[[], None] | None = None,
        cancel_retry_claim: dict | None = None,
        planned_cancel_claim: PlannedLeaderCancelClaim | CloseEntryDeadlineCancelClaim | None = None,
        build_planned_cancel_claim: Callable[
            [ExecutionRecord], PlannedLeaderCancelClaim | PlannedLeaderExpiredEntryCancelClaim | CloseEntryDeadlineCancelClaim,
        ] | None = None,
    ) -> ExecutionRecord:
        """One guarded cancellation claim for the exact assessed native order.

        Purpose selection belongs to the runner. This primitive grants no
        replacement authority. A prior claim needs an explicit bounded native
        protective retry proof; default callers retain once-only cancellation.
        """
        with self._operation_lock:
            if sum(claim is not None for claim in (planned_cancel_claim, cancel_retry_claim, build_planned_cancel_claim)) > 1:
                raise ValueError("execution_claim_purposes_conflict")
            self._require_active(expected.intent)
            if self._new_orders_enabled:
                raise RuntimeError("MOCK_AUTOMATION_REQUIRES_CLOSED_ORDER_GATE")
            self._repository.bind_automation_control(spec_id=spec_id, control_revision=control_revision)
            try:
                with self._repository.mock_automation_read_scope() as reader:
                    reader_args = {} if reader is None else {'_reader': reader}
                    record = self._repository.load(expected.intent.intent_id, **reader_args)
                    if record is None:
                        raise KeyError(f"unknown order intent: {expected.intent.intent_id}")
                    if ((cancel_retry_claim is not None or build_planned_cancel_claim is not None)
                            and record.state in TERMINAL_ORDER_STATES):
                        return record  # REST resolved it; do not submit another cancellation.
                    history = self._repository.events(record.intent.intent_id, **reader_args)
                if cancel_retry_claim is None and any(event.get("event_type") == "CANCEL_STARTED" for event in history):
                    return record  # Includes ACK loss, rejection and a restored runtime.
                if cancel_retry_claim is not None:
                    responses=[row for row in history if row.get('event_type') in {
                        'CANCEL_STARTED','CANCEL_REJECTED','CANCEL_RESPONSE_UNKNOWN','CANCEL_ACCEPTED'}]
                    count=sum(row.get('event_type')=='CANCEL_STARTED' for row in history)
                    if (before_cancel_claim is None or not responses or responses[-1]['event_type']!='CANCEL_REJECTED'
                            or responses[-1]['event_id']!=cancel_retry_claim.get('reject_event_id')
                            or count+1!=cancel_retry_claim.get('attempt')):
                        raise ValueError('MOCK_PROTECTIVE_CANCEL_RETRY_NATIVE_RESPONSE_CHANGED')
                # REST recovery advances confirmation timestamps even without
                # changing exposure. Preserve every intent/state/fill field;
                # only monotonic confirmation time may advance.
                if (replace(record, last_broker_as_of=expected.last_broker_as_of,
                            updated_at=expected.updated_at) != expected
                        or (expected.last_broker_as_of is not None and (
                            record.last_broker_as_of is None
                            or record.last_broker_as_of < expected.last_broker_as_of))):
                    raise ValueError("MOCK_AUTOMATION_CANCEL_ASSESSED_ORDER_CHANGED")
                if record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
                    raise ValueError("MOCK_AUTOMATION_CANCEL_REQUIRES_RECONCILED_ORDER")
                if before_cancel_claim is not None:
                    before_cancel_claim()
                if cancel_retry_claim is not None:
                    self._repository.bind_automation_control(spec_id=spec_id,control_revision=control_revision,
                        cancel_retry_claim=cancel_retry_claim)
                if build_planned_cancel_claim is not None:
                    planned_cancel_claim = build_planned_cancel_claim(record)
                    if planned_cancel_claim is None:
                        raise ValueError("planned stop cancellation claim is missing")
                if planned_cancel_claim is not None:
                    from kiwoom_monitor.application.close_mock_execution import CloseEntryDeadlineCancelClaim
                    from kiwoom_monitor.application.mock_automation_risk import PlannedLeaderExpiredEntryCancelClaim

                    self._repository.bind_automation_control(
                        spec_id=spec_id, control_revision=control_revision,
                        planned_cancel_claim=planned_cancel_claim.to_dict(),
                        **({"plan_recovery": planned_cancel_claim.recovery}
                           if isinstance(planned_cancel_claim, (PlannedLeaderExpiredEntryCancelClaim,
                                                               CloseEntryDeadlineCancelClaim)) else {}),
                    )
                with self._observe_operation():
                    return self._lifecycle.cancel(
                        record.intent.intent_id, 0,
                        after_cancel_claim=self._repository.clear_automation_control,
                    )
            finally:
                self._repository.clear_automation_control()
                self._new_orders_enabled = False

    def _require_active(self, intent: OrderIntent, *, _connection: Any = None) -> None:
        with self._state_lock:
            if not self._active:
                raise RuntimeError("mock execution runtime has not acquired ownership")
            if intent.environment != self._environment or intent.account_ref != self.account_ref or intent.run_id != self.run_id:
                raise ValueError("intent does not belong to this mock runtime")
            if (intent.environment == 'mock' and intent.venue in {'NXT', 'SOR'}
                    and not self.supports_automation_venue(intent.venue)):
                raise ValueError(f'{intent.venue}_AUTOMATION_REQUIRES_LOCAL_FAKE_BROKER')
            connection_args = {} if _connection is None else {"_connection": _connection}
            if not self._repository.claim_runtime(
                self._environment, self.account_ref, self.run_id, self.owner_token,
                self._lease_now(), self._lease_seconds, **connection_args,
            ):
                self._active = False
                raise RuntimeError("mock execution ownership was lost")

    def supports_automation_venue(self, venue: str) -> bool:
        return self._lifecycle.supports_automation_venue(venue)


class ManualMockOrderGateway:
    """Authenticated manual command boundary; it never generates orders on its own."""

    def __init__(
        self,
        runtime: ExecutionRuntime,
        repository: ExecutionRepository,
        account_refresh: Callable[[], Awaitable[AccountSnapshot]],
        *,
        now_provider: Callable[[], datetime] = lambda: datetime.now(KST),
        automation_execution_cut: Callable[[], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._runtime = runtime
        self._repository = repository
        self._account_refresh = account_refresh
        self._automation_execution_cut = automation_execution_cut
        self._now = now_provider
        self._command_lock = asyncio.Lock()
        self._accepting = True
        self._commands: set[asyncio.Task[ExecutionRecord]] = set()
        self._drain_task: asyncio.Task[None] | None = None
        self._closed = False
        self._manual_submit_enabled = True

    def set_manual_submit_enabled(self, enabled: bool) -> None:
        self._manual_submit_enabled = bool(enabled)

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
        if not self._manual_submit_enabled:
            raise RuntimeError("MOCK_ACCOUNT_AUTOMATIC_MODE")
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

    async def manage_close_order(self, expected, *, spec_id, control_revision, prepare_management):
        return await self._command(lambda: self._manage_close_order(expected, spec_id, control_revision, prepare_management))

    def _verified_order_management_cut(self, record, cut, spec_id, control_revision):
        from kiwoom_monitor.application.minute_order_management import verified_current_order_peers
        from kiwoom_monitor.infrastructure.persistence.execution_repository import project_execution_record
        observation, risk = cut['observation'], cut['risk']
        context = getattr(self._repository, 'selected_management_context', None)
        if context is not None:
            control = self._repository.current_automation_control(record.intent.account_ref)
            if control is None or control['control_revision'] != control_revision or context.spec_id != spec_id:
                raise ValueError('CURRENT_ORDER_PEER_CONTROL_CHANGED')
            peers = verified_current_order_peers(record, cut['recovery'], risk, observation.open_orders, control,
                selected_management_context=context, selected_management_policy=self._repository.selected_management_policy)
            return {**cut, 'verified_peer_orders': peers, 'member_record': record}
        projection = cut.get('member_projection', {})
        original = record
        record = project_execution_record(record, projection)
        control = None
        if getattr(record, 'member_spec_id', None) is not None:
            control = self._repository.current_automation_control(record.intent.account_ref)
            if (control is None or control['control_revision'] != control_revision
                    or record.member_spec_id != spec_id):
                raise ValueError('CURRENT_ORDER_PEER_CONTROL_CHANGED')
            if (projection and cut.get('member_projection_selection_id')
                    != (control.get('account_selection') or {}).get('selection_id')):
                raise ValueError('CURRENT_ORDER_PEER_CONTROL_CHANGED')
        peers = verified_current_order_peers(record, cut['recovery'], risk, observation.open_orders, control)
        if original.intent.intent_id in projection:
            peers = {**peers, 'legacy_evidence_scope': True}
        return {**cut, 'verified_peer_orders': peers, 'member_record': record}

    async def _manage_close_order(self, expected, spec_id, control_revision, prepare_management):
        async with self._command_lock:
            account = await self._account_refresh()
            cut = self._automation_execution_cut() if self._automation_execution_cut is not None else None
            def build_claim(record):
                observation = cut.get('observation') if cut is not None else None
                risk = cut.get('risk') if cut is not None else None
                if (observation is None or risk is None or observation.account != account
                        or observation.risk_snapshot != risk or risk.account_as_of != account.as_of
                        or observation.unmapped_broker_order_ids):
                    raise ValueError('CLOSE current-order requires a refreshed owner cut')
                verified_cut = self._verified_order_management_cut(record, cut, spec_id, control_revision)
                claim = prepare_management(verified_cut['member_record'], verified_cut)
                if not observation.reconciliation_complete or not observation.open_orders_complete:
                    if (claim is None or claim.assessment['action'] != 'CANCEL'
                            or set(observation.reasons) != {'unconfirmed_or_cancel_pending_order'}
                            or record.state is not OrderState.CANCEL_PENDING):
                        raise ValueError('CLOSE current-order requires a refreshed owner cut')
                return claim
            return await asyncio.to_thread(self._runtime.manage_close_order, expected, spec_id=spec_id,
                                            control_revision=control_revision, build_claim=build_claim)

    async def cancel_automation_intent(
        self, expected: ExecutionRecord, *, spec_id: str, control_revision: int,
        before_cancel_claim: Callable[[], None] | None = None,
        cancel_retry_claim: dict | None = None,
        planned_cancel_claim: PlannedLeaderCancelClaim | None = None,
        planned_stop_cancel_request: PlannedLeaderStopCancelRequest | None = None,
        expired_entry_plan: OrderPlanRef | None = None,
        prepare_plan_cancel: Callable[[ExecutionRecord, Mapping[str, Any]], Any] | None = None,
    ) -> ExecutionRecord:
        """Reuse account refresh, command serialization and credential drain."""
        return await self._command(lambda: self._cancel_automation_intent(
            expected, spec_id=spec_id, control_revision=control_revision,
            before_cancel_claim=before_cancel_claim,
            **({'cancel_retry_claim':cancel_retry_claim} if cancel_retry_claim is not None else {}),
            **({"planned_cancel_claim": planned_cancel_claim} if planned_cancel_claim is not None else {}),
            **({"planned_stop_cancel_request": planned_stop_cancel_request} if planned_stop_cancel_request is not None else {}),
            **({"expired_entry_plan": expired_entry_plan} if expired_entry_plan is not None else {}),
            **({"prepare_plan_cancel": prepare_plan_cancel} if prepare_plan_cancel is not None else {}),
        ))

    async def _cancel_automation_intent(
        self, expected: ExecutionRecord, *, spec_id: str, control_revision: int,
        before_cancel_claim: Callable[[], None] | None = None,
        cancel_retry_claim: dict | None = None,
        planned_cancel_claim: PlannedLeaderCancelClaim | None = None,
        planned_stop_cancel_request: PlannedLeaderStopCancelRequest | None = None,
        expired_entry_plan: OrderPlanRef | None = None,
        prepare_plan_cancel: Callable[[ExecutionRecord, Mapping[str, Any]], Any] | None = None,
    ) -> ExecutionRecord:
        async with self._command_lock:
            if sum(claim is not None for claim in (
                planned_stop_cancel_request, expired_entry_plan, planned_cancel_claim, cancel_retry_claim, prepare_plan_cancel,
            )) > 1:
                raise ValueError("execution_claim_purposes_conflict")
            current, history = await asyncio.to_thread(self._read_automation_cancel, expected)
            already_claimed = any(event.get("event_type") == "CANCEL_STARTED" for event in history)
            build_claim = None
            account, cut = None, None
            plan = planned_stop_cancel_request.plan if planned_stop_cancel_request is not None else expired_entry_plan
            if plan is not None:
                if (not isinstance(current.intent, PlannedOrderIntent) or current.intent.intent_id != plan.plan_id
                        or (plan.plan_id, plan.policy_ref) != (current.intent.plan.plan_id, current.intent.plan.policy_ref)):
                    raise ValueError("planned stop cancellation root changed")
                allowed_states = ({OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
                                  if expired_entry_plan is not None else {OrderState.PARTIALLY_FILLED})
                if (not already_claimed and current.state not in TERMINAL_ORDER_STATES
                        and (current.state not in allowed_states or self._automation_execution_cut is None)):
                    raise ValueError("planned cancellation requires a refreshed owner cut")
            if (current.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
                    and (cancel_retry_claim is not None or not already_claimed)):
                account = await self._account_refresh()
                if plan is not None or prepare_plan_cancel is not None:
                    if self._automation_execution_cut is None:
                        raise ValueError('planned cancellation requires a refreshed owner cut')
                    cut = self._automation_execution_cut()

            if prepare_plan_cancel is not None:
                def build_claim(record):
                    observation = cut.get('observation') if cut is not None else None
                    risk = cut.get('risk') if cut is not None else None
                    if (account is None or observation is None or risk is None
                            or observation.risk_snapshot != risk or observation.account != account
                            or risk.account_as_of != account.as_of or not observation.open_orders_complete
                            or observation.unmapped_broker_order_ids):
                        raise ValueError('planned cancellation requires a refreshed owner cut')
                    verified_cut = self._verified_order_management_cut(record, cut, spec_id, control_revision)
                    return prepare_plan_cancel(verified_cut['member_record'], verified_cut)
            if plan is not None:
                def build_claim(record):
                    observation = cut.get("observation") if cut is not None else None
                    risk = cut.get("risk") if cut is not None else None
                    if (account is None or observation is None or risk is None or observation.risk_snapshot != risk
                            or observation.account != account or risk.account_as_of != account.as_of
                            or not observation.reconciliation_complete or not observation.open_orders_complete
                            or observation.unmapped_broker_order_ids):
                        raise ValueError("planned cancellation requires a refreshed owner cut")
                    self._verified_order_management_cut(record, cut, spec_id, control_revision)
                    if planned_stop_cancel_request is not None:
                        return planned_stop_cancel_request.claim_from(risk)
                    from kiwoom_monitor.application.mock_automation_risk import PlannedLeaderExpiredEntryCancelClaim

                    return PlannedLeaderExpiredEntryCancelClaim(
                        plan, risk.snapshot_id, risk.reconciliation_revision, cut["recovery"],
                        detailed_quantity_at_cancel=record.detailed_filled_quantity,
                    )
            return await asyncio.to_thread(
                self._runtime.cancel_automation_intent, expected,
                spec_id=spec_id, control_revision=control_revision,
                before_cancel_claim=before_cancel_claim,
                **({'cancel_retry_claim':cancel_retry_claim} if cancel_retry_claim is not None else {}),
                **({"planned_cancel_claim": planned_cancel_claim} if planned_cancel_claim is not None else {}),
                **({"build_planned_cancel_claim": build_claim} if build_claim is not None else {}),
            )

    def _read_automation_cancel(self, expected: ExecutionRecord) -> tuple[ExecutionRecord, tuple[dict, ...]]:
        with self._repository.mock_automation_read_scope() as reader:
            reader_args = {} if reader is None else {'_reader': reader}
            current = self.load(expected.intent.intent_id, **reader_args)
            if current.intent != expected.intent:
                raise ValueError("MOCK_AUTOMATION_CANCEL_ASSESSED_ORDER_CHANGED")
            return current, self._repository.events(current.intent.intent_id, **reader_args)

    async def _cancel(self, intent_id: str, quantity: int) -> ExecutionRecord:
        async with self._command_lock:
            await asyncio.to_thread(self.load, intent_id)
            # A fresh broker snapshot prevents a filled order from being cancelled
            # based only on an older local state.
            await self._account_refresh()
            return await asyncio.to_thread(self._runtime.cancel, intent_id, quantity)

    def load(self, intent_id: str, *, _reader=None) -> ExecutionRecord:
        reader_args = {} if _reader is None else {'_reader': _reader}
        record = self._repository.load(intent_id, **reader_args)
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
