"""Stateful execution episode for one admitted automatic mock-account strategy."""

from __future__ import annotations

from .market_observations import KST, as_kst

import asyncio
import hashlib
import json
import logging
from contextlib import nullcontext
from copy import deepcopy
from .database_observation_readers import OBSERVATION_DELIVERY_PROTOCOL
from .execution_runtime import AutomationViBuyBlocked
from .observation_frame_recovery import (
    ObservationFrameRecovery, drain_thread_call,
)
from time import perf_counter
from threading import Lock
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    StrategyDecision, StrategyState, session_entry_time_rejection,
)
from kiwoom_monitor.application.mock_automation_execution import (
    MockAutomationRiskSuperseded,
    assess_mock_unfilled_gate_bundle_from_risk,
    dispatch_mock_automation_decision_from_risk,
    dispatch_mock_unfilled_replacement_from_risk,
    unfilled_execution_policy_rejections,
    unfilled_management_control_revision, unfilled_registration_policy,
    emergency_stop_mock_automation,
    mock_automation_intent_id,
)
from kiwoom_monitor.application.mock_automation_session_clock import (
    SESSION_CLOCK_VERSION, clock_exit_decision, session_clock_assessment,
)
from kiwoom_monitor.application.order_lifecycle import (
    prepare_unfilled_management_preflight, validate_unfilled_management_preflight,
    UNFILLED_ATTEMPT_PLAN_VERSION, evaluate_unfilled_order_management_shadow,
    project_unfilled_native_attempts, seal_unfilled_attempt_plan, validate_unfilled_attempt_plan,
    anchored_unfilled_policy, protective_cancel_recovery, unfilled_plan_intent,
    prepare_unfilled_reprice_evidence,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    record_mock_automation_recovery_from_risk,
)
from kiwoom_monitor.application.mock_automation_admission import (
    MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION, MOCK_AUTOMATION_ADMISSION_VERSION, MockAutomationAdmission,
)
from kiwoom_monitor.application.mock_automation_risk import mock_automation_protection_trigger
from kiwoom_monitor.application.research_families import get_research_family, parse_strategy_config
from kiwoom_monitor.application.leader_breakout import (
    LeaderBreakoutConfig, LeaderTradeInputs, leader_evaluation, leader_position_proof,
    replay_leader_stop_observation, validate_leader_position_proof, leader_current_entry_matches,
    leader_position_action_ref,
)
from kiwoom_monitor.application.research_replay import (
    KrxMinuteBarFrame, MinuteStrategyInputs,
)
from kiwoom_monitor.domain.execution_activation import CLOSE_NATIVE_PLAN_MODE, LEADER_NATIVE_PLAN_MODE, PANIC_NATIVE_PLAN_MODE, MockAutomationOperatingSpec
from kiwoom_monitor.domain.order_contract import OrderIntent, OrderPlanRef, OrderSide, OrderState, OrderType, PlannedOrderIntent, TERMINAL_ORDER_STATES
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


logger = logging.getLogger("kiwoom_monitor.central_server.mock_automation_runner")
RUNNER_CHECKPOINT_VERSION = "mock_automation_runner_checkpoint/v1"
INPUT_KINDS = ("top20_membership", "minute_bar")
CLOCK_COLLECTION = "execution_mock_automation_clock_current"
PROTECTION_REVIEW_VERSION = "mock-protection-review/v1"


def _checkpoint_bar_document(value):
    if type(value) is not KrxMinuteBarFrame:
        return asdict(value)
    document = {}
    for field in fields(value):
        item = getattr(value, field.name)
        item_type = type(item)
        if not (item is None or item_type is str or item_type is int or item_type is bool or item_type is float):
            return asdict(value)
        document[field.name] = item
    return document


class MockAutomationEpisodePosition:
    """Own actual FILL/strategy position transitions and their RAM publication fence.

    Sibling Episode owners keep input progress and aggregate checkpoint ACK.
    Native authority remains with the existing execution runtime and gates.
    Borrowed ledger/clock operations are used during a call and never retained.
    """

    def __init__(self, *, account_ref: str, run_id: str, member_spec_id: str | None = None,
                 member_projection_source=None) -> None:
        self._account_ref = account_ref
        self._run_id = run_id
        self._member_spec_id = member_spec_id
        self._member_projection_source = member_projection_source
        self._fill_cursor = 0
        self._state = StrategyState()
        self._position_state_lock = Lock()
        self._position_state_writers = self._position_state_revision = 0
        self._position_state_available_at = None
        self._position_state_failed = False
        self._seen_fill_ids: set[str] = set()
        self._plan_fill_listener = None

    def set_plan_fill_listener(self, listener):
        """Borrow the existing minute-plan owner; notification performs no I/O."""
        self._plan_fill_listener = listener

    def bind_member_scope(self, spec_id, *, member_projection_source=None):
        """Change only an explicitly retained read scope while its Runner is drained."""
        with self._position_state_lock:
            if self._member_spec_id not in (None, spec_id) or self._position_state_writers:
                raise RuntimeError('MOCK_AUTOMATION_POSITION_SCOPE_BUSY_OR_CHANGED')
            if (self._member_spec_id == spec_id and self._member_projection_source == member_projection_source):
                return
            self._member_spec_id = spec_id
            self._member_projection_source = member_projection_source
            self._position_state_revision += 1

    @property
    def state(self) -> StrategyState:
        return self._state

    @property
    def fill_cursor(self) -> int:
        return self._fill_cursor

    def replace_strategy_state(self,state, *, position_time):
        available=position_time()
        with self._position_state_lock:
            self._state=state
            self._position_state_revision+=1
            self._position_state_available_at=available


    def apply_new_fills(self, repository, *, leader, config, position_time, _reader=None) -> None:
        # A missing ownership proof is a read-preparation failure. Do not consume
        # an unscoped event or advance the cursor while waiting for that proof.
        projection = self._member_projection_source() if self._member_projection_source is not None else None
        # Fence only publication of copied facts. Never hold this lock across I/O.
        with self._position_state_lock:
            if self._position_state_writers:self._position_state_failed=True
            self._position_state_writers+=1
            before=(self._state,self._fill_cursor)
        try:
            self._apply_new_fills_core(repository, leader=leader, config=config, _reader=_reader,
                                       member_projection=projection)
            available=position_time()
            with self._position_state_lock:
                if before!=(self._state,self._fill_cursor) or self._position_state_available_at is None:
                    self._position_state_available_at=available
        except BaseException:
            with self._position_state_lock:self._position_state_failed=True
            raise
        finally:
            with self._position_state_lock:
                self._position_state_revision+=1
                self._position_state_writers-=1


    def _apply_new_fills_core(self, repository, *, leader, config, _reader=None, member_projection=None) -> None:
        reader_args = {} if _reader is None else {'_reader': _reader}
        if member_projection is not None:
            reader_args['member_projection'] = member_projection
        while True:
            page = repository.account_events(
                "mock", self._account_ref,
                after_sequence=self._fill_cursor, limit=1000, **reader_args,
            )
            for event in page.events:
                self._fill_cursor = max(self._fill_cursor, event.accepted_sequence)
                fill_key = (json.dumps([event.intent_id, event.broker_order_id, event.broker_execution_id], separators=(',', ':'))
                            if event.broker_order_id else event.broker_execution_id)
                if (
                    event.run_id != self._run_id
                    or self._member_spec_id is not None and getattr(event, 'member_spec_id', None) != self._member_spec_id
                    or event.event_type != "FILL"
                    or not event.broker_execution_id
                    or fill_key in self._seen_fill_ids
                    or event.quantity <= 0
                    or event.price <= 0
                ):
                    continue
                self._seen_fill_ids.add(fill_key)
                if self._plan_fill_listener is not None and getattr(event, 'plan', None) is not None:
                    self._plan_fill_listener(event.plan)
                if event.side == "BUY":
                    old_quantity = (
                        int(self._state.position_quantity or 0)
                        if self._state.status == "open" and self._state.symbol == event.symbol else 0
                    )
                    old_price = int(self._state.entry_price or 0)
                    quantity = old_quantity + event.quantity
                    from kiwoom_monitor.application.mock_automation_risk import mock_owned_buy_basis
                    price = mock_owned_buy_basis(old_quantity, old_price, event.quantity, event.price)
                    self._state = StrategyState(
                        status="open", symbol=event.symbol, entry_price=price,
                        position_quantity=quantity,
                        opened_at=(self._state.opened_at if old_quantity else event.occurred_at.isoformat()),
                        emitted_candidate_keys=self._state.emitted_candidate_keys,
                    )
                elif self._state.status == "open" and self._state.symbol == event.symbol:
                    remaining = max(0, int(self._state.position_quantity or 0) - event.quantity)
                    if remaining:
                        self._state = StrategyState(
                            status="open", symbol=self._state.symbol,
                            entry_price=self._state.entry_price,
                            position_quantity=remaining, opened_at=self._state.opened_at,
                            emitted_candidate_keys=self._state.emitted_candidate_keys,
                        )
                    else:
                        from kiwoom_monitor.application.leader_breakout import leader_reentry_enabled
                        boundary = (max(event.occurred_at, event.received_at).isoformat()
                                    if leader and leader_reentry_enabled(config) else '')
                        self._state = StrategyState(cooldown_until=boundary,
                            emitted_candidate_keys=self._state.emitted_candidate_keys) if leader else StrategyState(
                            status="cooldown",
                            cooldown_until=(event.occurred_at + timedelta(
                                seconds=int(config.cooldown_seconds),
                            )).isoformat(),
                            emitted_candidate_keys=self._state.emitted_candidate_keys,
                        )
            if not page.has_more:
                break


    def matches_risk(self, risk: Any) -> bool:
        try:
            owned = dict(risk.owned_for_member(self._member_spec_id) if self._member_spec_id is not None
                         else risk.owned_position_quantities)
        except ValueError:
            return False
        if self._state.status == "open":
            return owned == {self._state.symbol: int(self._state.position_quantity or 0)}
        return not owned


    def restore_fill_cursor(self, value) -> None:
        self._fill_cursor = max(0, int(value))

    def restore_strategy_state(self, value) -> None:
        # Decode at the original checkpoint statement; no publication update.
        state = dict(value)
        state["emitted_candidate_keys"] = tuple(state.get("emitted_candidate_keys", ()))
        self._state = StrategyState(**state)

    def restore_seen_fill_ids(self, values) -> None:
        self._seen_fill_ids = set(str(value) for value in values)

    def checkpoint_fields(self) -> dict[str, Any]:
        return {
            "fill_cursor": self._fill_cursor,
            "strategy_state": self._state.to_dict(),
            "seen_fill_ids": sorted(self._seen_fill_ids),
        }

    def try_observation_cut(self):
        if not self._position_state_lock.acquire(blocking=False):
            return None
        try:
            if self._position_state_writers or self._position_state_failed or self._position_state_available_at is None:
                return None
            return (self._position_state_revision, self._state,
                    self._fill_cursor, self._position_state_available_at)
        finally:
            self._position_state_lock.release()

    def observation_cut_matches(self, cut) -> bool:
        if not self._position_state_lock.acquire(blocking=False):
            return False
        try:
            return not (self._position_state_writers or self._position_state_failed
                        or cut[0] != self._position_state_revision)
        finally:
            self._position_state_lock.release()


class MockAutomationInputProgress:
    """Own native input progress and bootstrap/repair publication transitions.

    Existing adapters own their windows; the Episode composes this progress
    with its position and checkpoint owners. Native authority stays separate.
    Operation capabilities are borrowed, not retained.
    """

    def __init__(self) -> None:
        self._cursor = 0
        self._bootstrap_pending = True
        self._delivery_protocol = None
        self._frame_recovery = None
        self._frame_recovery_receipt = None

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def bootstrap_pending(self) -> bool:
        return self._bootstrap_pending

    @property
    def recovering(self) -> bool:
        return self._frame_recovery is not None

    def recovery_receipt(self):
        return self._frame_recovery.receipt()

    def advance_consumed(self, observation) -> None:
        self._cursor = max(self._cursor, int(observation.get("accepted_sequence", 0)))

    def restore_cursor(self, value) -> None:
        self._cursor = max(0, int(value))

    def use_native_protocol(self, protocol):
        self._bootstrap_pending = False
        self._delivery_protocol = protocol
        self._frame_recovery = None

    def restore_metadata(self, document, *, leader, inputs) -> None:
        self._delivery_protocol = document.get("delivery_protocol")
        self._frame_recovery_receipt = document.get("input_recovery")
        self._bootstrap_pending = bool(document.get("protection_resume_requires_bootstrap", False))
        if self._delivery_protocol is None and not leader:
            self._frame_recovery = ObservationFrameRecovery(
                self._cursor, inputs.session_profile,
                inputs.keep_bars, inputs.universe_horizon, False,
            )

    def checkpoint_incomplete(self, *, recovery=None) -> bool:
        return self._bootstrap_pending or (self._frame_recovery is not None and recovery is None)

    def checkpoint_protocol_fields(self, *, recovery=None) -> dict[str, Any]:
        value = {}
        if recovery is not None:
            value["delivery_protocol"] = OBSERVATION_DELIVERY_PROTOCOL
            value["input_recovery"] = recovery.receipt()
        elif self._delivery_protocol is not None:
            value["delivery_protocol"] = self._delivery_protocol
            if self._frame_recovery_receipt is not None:
                value["input_recovery"] = deepcopy(self._frame_recovery_receipt)
        return value

    def project_incomplete_checkpoint(self, content) -> None:
        # Only the input-owned fields of this detached aggregate document.
        content["protection_resume_requires_bootstrap"] = self._bootstrap_pending
        if self._frame_recovery is not None:
            content.pop("delivery_protocol", None)
            content.pop("input_recovery", None)

    def bootstrap(self, store, inputs, *, leader, publish_status, checkpoint) -> bool:
        if leader:
            self._bootstrap_pending = False
            self._delivery_protocol = "leader_0B/v1"
            publish_status({"state": "WARMUP", "reason": "awaiting_fresh_approved_0B", "input_mode": "0B", "orders_enabled": False})
            checkpoint()
            return True
        page = store.load_observation_bootstrap(INPUT_KINDS, per_kind_limit=5000)
        if not page.ready:
            publish_status({
                "state": "WARMUP", "reason": "bootstrap_pending:" + page.reason,
                "orders_enabled": False,
            })
            return False
        seed = page.rows
        self._cursor = max((int(value.get("accepted_sequence", 0)) for value in seed), default=0)
        inputs.seed(seed)
        publish_status({
            "state": "WARMUP", "reason": "bootstrapped_without_historical_orders",
            "orders_enabled": False,
        })
        self._delivery_protocol = OBSERVATION_DELIVERY_PROTOCOL
        self._bootstrap_pending = False
        try:
            checkpoint()
        except Exception:
            self._bootstrap_pending = True
            raise
        return True


    def repair(self, store, inputs, limit: int, *, checkpoint) -> None:
        recovery = self._frame_recovery
        if recovery.advance(store, INPUT_KINDS, limit):
            checkpoint(recovery=recovery)
            inputs.install_frames(bars=recovery.bars, universes=recovery.universes)
            self._delivery_protocol = OBSERVATION_DELIVERY_PROTOCOL
            self._frame_recovery_receipt = recovery.receipt()
            self._frame_recovery = None



class MockAutomationCheckpoint:
    """Own one episode's aggregate checkpoint attempts and acknowledged content.

    The Episode projects its current owners and preserves native operation order.
    The Runner owns the scheduled main task and its drain.
    This owner advances the attempt generation before the borrowed write, and
    releases pending only after that write returns. Store and clock capabilities
    are borrowed per commit; no runtime authority, task or lock is retained.
    """

    def __init__(self) -> None:
        self._revision = 0
        self._pending = False
        self._acknowledged_content: dict[str, Any] | None = None

    @property
    def pending(self) -> bool:
        return self._pending

    @property
    def revision(self) -> int:
        return self._revision

    def restore_revision(self, value) -> None:
        self._revision = max(0, int(value))

    def commit(self, content, store, *, account_ref, now_provider, key="current",
               force: bool = False) -> None:
        if not force and not self._pending and content == self._acknowledged_content:
            return  # Only this runner's acknowledged content can be a no-op.
        self._pending = True
        # Keep the existing monotonically increasing attempt generation, including
        # ambiguous commit outcomes. Pending/last-content track acknowledgement.
        self._revision += 1
        document = {
            **content, "checkpoint_revision": self._revision,
            "saved_at": now_provider().isoformat(),
        }
        store.upsert_documents(
            "execution_mock_automation_runner_current",
            [{"owner": account_ref, "key": key, "document": document}],
        )
        self._acknowledged_content = content
        self._pending = False


class MockAutomationEpisode:
    """Consume new NAS observations once and pass actionable Decisions to O1.

    The strategy's open position is reconstructed only from detailed O1 FILL
    events.  A signal or an accepted order never creates a synthetic position.
    """

    def __init__(
        self,
        store: Any,
        bundle: Any,
        spec: MockAutomationOperatingSpec,
        *,
        price_source=None,
        nxt_price_source=None,
        leader_context_source=None,
        now_provider=None,
        member_admission: MockAutomationAdmission | None = None,
        proposed_selection=None,
        minute_candidate_current=None, close_calendar=None,
        market_comparison=None,
    ) -> None:
        context = bundle.automation_context
        if (
            context is None
            or bundle.account_ref != spec.account_scope.account_ref
            or bundle.run_id != context.execution_run_id
            or member_admission is None and context.spec_id != spec.spec_id
        ):
            raise ValueError("MOCK_AUTOMATION_RUNNER_CONTEXT_MISMATCH")
        if spec.supported_venue in {'NXT', 'SOR'} and (
                nxt_price_source is None or not bundle.runtime.supports_automation_venue(spec.supported_venue)):
            raise ValueError(f'{spec.supported_venue}_AUTOMATION_REQUIRES_LOCAL_FAKE_BROKER_AND_QUOTE_SOURCE')
        if member_admission is not None and (
                not isinstance(member_admission, MockAutomationAdmission)
                or member_admission.version not in {MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION, MOCK_AUTOMATION_ADMISSION_VERSION}
                or (member_admission.account_ref, member_admission.execution_run_id,
                    member_admission.spec_id, member_admission.strategy_ref, member_admission.candidate_package_hash,
                    member_admission.final_result_hash, member_admission.final_batch_id, member_admission.final_run_id)
                != (bundle.account_ref, bundle.run_id, spec.spec_id, spec.strategy_ref, spec.candidate_package_hash,
                    spec.final_result_hash, spec.final_batch_id, spec.final_run_id)):
            raise ValueError("MOCK_AUTOMATION_MEMBER_CONTEXT_MISMATCH")
        self._store = store
        self._minute_candidate_current = minute_candidate_current
        self._close_calendar = close_calendar
        self._now = (lambda: as_kst(now_provider())) if now_provider else (lambda: datetime.now(KST))
        self._bundle = bundle
        self._spec = spec
        self._selected_management_context = getattr(bundle, 'selected_management_context', None)
        if self._selected_management_context is not None:
            from kiwoom_monitor.application.mock_automation_risk import mock_automation_expected_binding_revision
            mock_automation_expected_binding_revision(spec, self._selected_management_context)
            if self._selected_management_context.execution_run_id != bundle.run_id:
                raise ValueError('MOCK_SELECTED_MANAGEMENT_RUN_CHANGED')
        self._member_admission = member_admission
        self._account_selection = proposed_selection
        self._admitted_member_spec_ids = ()
        self._admission_id = (member_admission.admission_id if member_admission is not None
                              else getattr(context, 'admission_id', ''))
        if self._selected_management_context is not None:
            self._admission_id = self._selected_management_context.admission_id
        self._checkpoint_key = ('member:' + spec.spec_id if member_admission is not None
                                and member_admission.version == MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION else 'current')
        if self._selected_management_context is not None:
            self._checkpoint_key = self._selected_management_context.checkpoint_key
        self._repository = ForwardEvaluationRepository(store,
            **({'selected_management_context': self._selected_management_context}
               if self._selected_management_context is not None else {}))
        self._selected_management_source_risk_ids = None
        self._position_registered_implementation = ""
        self._position_policy_implementation = None
        self._family = None
        self._config = None
        self._leader = False
        self._leader_context_source = leader_context_source
        self._market_comparison = market_comparison
        self._entry_market_comparison = None
        self._strategy_inputs = None
        self._leader_position_proof = None
        self._leader_plan_mode = self._spec.execution_plan_mode == LEADER_NATIVE_PLAN_MODE
        self._panic_plan_mode = self._spec.execution_plan_mode == PANIC_NATIVE_PLAN_MODE
        self._minute_plan_mode = self._spec.execution_plan_mode in {CLOSE_NATIVE_PLAN_MODE, PANIC_NATIVE_PLAN_MODE}
        self._minute_management = None
        self._leader_plan_root = None
        self._leader_plan_deadline = None
        self._leader_plan_cancel_attempt = None
        self._leader_management_input = None
        self._leader_management_records = None
        self._leader_plan_refresh_quote = None
        self._leader_plan_confirmation = None
        self._leader_staged_facts = None
        self._leader_plan_reprepare_requested = None
        self._planned_protection_restore = None
        self._planned_protection_readback = None
        self._price_source = nxt_price_source if spec.supported_venue == 'NXT' else price_source
        self._nxt_price_source = nxt_price_source
        self._unfilled_cost_model = None
        self._unfilled_population = None
        self._unfilled_evidence_cache = None
        self._unfilled_judgment = None
        self._unfilled_ordinary_ready = None
        self._unfilled_alignment_ref = None
        self._unfilled_alignment_price = None
        self._unfilled_registration = None
        self._cancel_recovery = None
        self._unfilled_source_status = None
        self._unfilled_source_pending = True
        self._unfilled_source_facts = None
        self._unfilled_source_generation = 0
        self._unfilled_discovery_pending = True
        self._unfilled_source_deadline = None
        self._unfilled_source_recovery_deadline = None
        self._unfilled_source_price_ready = None
        self._protection_exit = None
        self._protection_exit_price = None
        self._protection_exit_facts = None
        self._protection_exit_recovery_revision = None
        self._protection_exit_recovery_checked = False
        self._input_progress = MockAutomationInputProgress()
        self._checkpoint = MockAutomationCheckpoint()
        self._episode_position = MockAutomationEpisodePosition(
            account_ref=bundle.account_ref, run_id=bundle.run_id,
            member_spec_id=(self._selected_management_context.member_spec_id
                if self._selected_management_context is not None else spec.spec_id if member_admission is not None else None),
            member_projection_source=(self._position_member_projection if member_admission is not None
                and member_admission.version == MOCK_AUTOMATION_ADMISSION_VERSION else None),
        )
        self._status: dict[str, Any] = {
            "state": "WARMUP", "reason": "not_started", "orders_enabled": False,
        }
        self._latency: dict[str, Any] | None = None
        self._closing = False
        self._session_clock: dict[str, Any] | None = None
        self._unfilled_preflight: dict[str, Any] | None = None
        self._unfilled_attempt_plan: dict[str, Any] | None = None
        self._unfilled_preflight_lock = Lock()
        self._unfilled_input_lock = Lock()
        self._unfilled_input = None
        self._unfilled_input_identity = None
        self._unfilled_delivery = None
        self._unfilled_processed_cause = None
        self._unfilled_processed_deadline = None
        self._protection_lock = Lock()
        self._protection_review: dict[str, Any] | None = None
        self._stop_loss_cut = None
        self._native_risk_delivery = None
        self._prepared = False
        self._scheduled_task_present = None
        self._capture_wake = None


    @property
    def prepared(self) -> bool:
        return self._prepared

    @property
    def spec_id(self) -> str:
        return self._spec.spec_id

    @property
    def admitted_member_spec_ids(self) -> tuple[str, ...]:
        return self._admitted_member_spec_ids

    @property
    def admission_ref(self):
        from kiwoom_monitor.domain.execution_activation import MockAutomationRetainedAdmissionRef
        admission = self._member_admission
        if self._selected_management_context is not None:
            admission = self._repository.load_mock_automation_admission_for_spec(self._bundle.account_ref, self._spec.spec_id)
            context = self._selected_management_context
            if (admission is None or (admission.admission_id, admission.spec_id, admission.execution_run_id,
                    admission.account_ref, admission.version, admission.account_selection_id)
                    != (context.admission_id, context.spec_id, context.execution_run_id, context.account_ref,
                        context.source_admission_version, context.source_account_selection_id)):
                raise ValueError('MOCK_SELECTED_MANAGEMENT_ADMISSION_CHANGED')
        if admission is None:
            admission = self._repository.load_mock_automation_admission_for_spec(
                self._bundle.account_ref, self._spec.spec_id)
            if (admission is None or admission.version != MOCK_AUTOMATION_ADMISSION_VERSION
                    or (admission.account_ref, admission.execution_run_id, admission.spec_id,
                        admission.strategy_ref, admission.candidate_package_hash,
                        admission.final_result_hash, admission.final_batch_id, admission.final_run_id)
                    != (self._bundle.account_ref, self._bundle.run_id, self._spec.spec_id,
                        self._spec.strategy_ref, self._spec.candidate_package_hash,
                        self._spec.final_result_hash, self._spec.final_batch_id, self._spec.final_run_id)
                    or self._admission_id and admission.admission_id != self._admission_id):
                raise ValueError('MOCK_AUTOMATION_MEMBER_ADMISSION_CHANGED')
            self._admission_id = admission.admission_id
        return MockAutomationRetainedAdmissionRef(self._spec.spec_id, admission.admission_id,
            admission.account_selection_id, admission.execution_run_id)

    def apply_account_selection(self, control):
        """Adopt an acknowledged composition without resetting an original cue or state."""
        selection = control.account_selection if control is not None else None
        if (selection is None or control.execution_run_id != self._bundle.run_id
                or control.active_spec_id != self._bundle.automation_context.spec_id
                or not self._selection_matches_admission(selection)):
            raise ValueError('MOCK_AUTOMATION_MEMBER_ADMISSION_CHANGED')
        admission = self._member_admission
        if self._member_admission is None:
            admission = self._repository.load_mock_automation_admission_for_spec(self._bundle.account_ref, self._spec.spec_id)
            if (admission is None or admission.admission_id != self._admission_id
                    or not selection.matches_admission(spec_id=self._spec.spec_id, admission_id=admission.admission_id,
                        original_selection_id=getattr(admission, 'account_selection_id', None),
                        execution_run_id=admission.execution_run_id)):
                raise ValueError('MOCK_AUTOMATION_MEMBER_ADMISSION_CHANGED')
        self._episode_position.bind_member_scope(self._spec.spec_id,
            member_projection_source=(self._position_member_projection
                if admission.version == MOCK_AUTOMATION_ADMISSION_VERSION else None))
        self._account_selection = selection
        self._admitted_member_spec_ids = selection.member_spec_ids
        self._member_admission = admission

    def _position_member_projection(self):
        if self._account_selection is None:
            raise RuntimeError('MOCK_RETAINED_MEMBER_PROJECTION_UNAVAILABLE')
        return self._bundle.monitor.member_projection_for_selection(self._account_selection.selection_id)

    def _selection_matches_admission(self, selection):
        ref = self.admission_ref
        return selection.matches_admission(spec_id=ref.spec_id, admission_id=ref.admission_id,
            original_selection_id=ref.original_selection_id, execution_run_id=ref.execution_run_id)

    def _allows_entry(self, control):
        if self._selected_management_context is not None:
            return False
        return control.account_selection is None or control.account_selection.allows_entry(self._spec.spec_id)

    def _matches_control(self, control) -> bool:
        if (control is None or getattr(control, 'execution_run_id', self._bundle.run_id) != self._bundle.run_id):
            return False
        if self._selected_management_context is not None:
            return (control.active_spec_id == self._selected_management_context.spec_id
                    and control.execution_run_id == self._selected_management_context.execution_run_id
                    and control.entry_resume_required)
        if self._member_admission is None:
            return control.active_spec_id == self._spec.spec_id
        selection = control.account_selection
        return (selection is not None and self._selection_matches_admission(selection)
                and (self._account_selection is None or self._account_selection == selection)
                and control.active_spec_id == self._bundle.automation_context.spec_id
                and control.includes_spec(self._spec.spec_id))

    def _checkpoint_document(self, collection):
        if self._selected_management_context is not None:
            row = self._store.load_document(collection, self._bundle.account_ref, self._checkpoint_key)
            if row is not None:
                return row.get('document')
            policy = self._repository.selected_management_policy
            document = next((deepcopy(item['document']) for item in policy['documents']
                if (item['collection'], item['owner'], item['key'])
                == (collection, self._bundle.account_ref, self._checkpoint_key)), None)
            if document is not None and 'fill_cursor' in document:
                cursor = document['fill_cursor']
                coordinates = policy['installation']['event_coordinates']
                document['fill_cursor'] = max((item['target_accepted_sequence'] for item in coordinates
                    if item['source_accepted_sequence'] <= cursor), default=0)
            return document
        if self._member_admission is None:
            rows = self._store.load_documents(collection, self._bundle.account_ref, 1)
            return rows[0].get('document') if rows else None
        row = self._store.load_document(collection, self._bundle.account_ref, self._checkpoint_key)
        return row.get('document') if row else None

    def _original_policy_document(self, collection, key, *, _reader=None):
        args = {} if _reader is None else {'_reader': _reader}
        row = self._store.load_document(collection, self._bundle.account_ref, key, **args)
        if row is not None or self._selected_management_context is None:
            return row
        return next(({'document': deepcopy(item['document'])}
            for item in self._repository.selected_management_policy['documents']
            if (item['collection'], item['owner'], item['key'])
            == (collection, self._bundle.account_ref, key)), None)

    def _protection_policy_context(self, trigger):
        context = self._selected_management_context
        if context is not None:
            if self._selected_management_source_risk_ids is None:
                self._selected_management_source_risk_ids = frozenset(item['key']
                    for item in self._repository.selected_management_policy['documents']
                    if (item['collection'], item['owner']) ==
                    ('execution_mock_automation_risk_snapshots', context.account_ref))
            if trigger.get('snapshot_id') in self._selected_management_source_risk_ids:
                return None  # Archived trigger retains its original binding and hash.
        return context

    def _leader_evidence_key(self, decision_id):
        if self._selected_management_context is not None:
            return (mock_automation_intent_id(self._spec.spec_id, decision_id)
                    if self._selected_management_context.source_admission_version == MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION
                    else decision_id)
        return (mock_automation_intent_id(self._spec.spec_id, decision_id)
                if self._member_admission is not None and self._member_admission.version == MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION
                else decision_id)

    def _current_leader_context(self):
        if self._leader_context_source is None:
            return None
        return (self._leader_context_source(self._spec.spec_id) if self._member_admission is not None
                else self._leader_context_source())

    def _own_active_records(self, records):
        if self._member_admission is None:
            return records
        # Keep unattributed and foreign-run records as blockers. Peer ownership
        # comes from native records and the stored admitted membership.
        return tuple(record for record in records if not (
            getattr(record, 'member_spec_id', None) in self._admitted_member_spec_ids
            and record.member_spec_id != self._spec.spec_id
            and record.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
            and not (record.amendment is not None and record.amendment.reconciliation_pending)
            and record.intent.run_id == self._bundle.run_id
            and record.intent.account_ref == self._bundle.account_ref))

    def _record_recovery(self, recovery, risk):
        kwargs = {}
        if self._member_admission is not None:
            cut = self._bundle.monitor.latest_automation_execution_cut
            kwargs['observation'] = (cut['observation'] if cut is not None
                and cut['risk'] == risk and cut['recovery'] == recovery else None)
        return record_mock_automation_recovery_from_risk(self._repository, self._bundle.runtime,
            recovery, risk, account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id, **kwargs)

    async def _refresh_recovery_risk(self):
        recovery = await self._bundle.monitor.refresh_recovery()
        if self._member_admission is not None:
            cut = self._bundle.monitor.latest_automation_execution_cut
            if cut is None:
                raise RuntimeError('MOCK_AUTOMATION_ACCOUNT_CUT_UNAVAILABLE')
            return cut['recovery'], cut['risk']
        risk = await drain_thread_call(self._repository.load_latest_mock_automation_risk,
                                       self._bundle.account_ref)
        return recovery, risk

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def execution_busy(self) -> bool:
        return self._unfilled_preflight_lock.locked()

    @property
    def needs_native_execution_inputs(self) -> bool:
        return self._minute_plan_mode or self._spec.unfilled_execution_policy is not None

    def bind_execution_callbacks(self, *, scheduled_task_present, capture_wake) -> None:
        # Narrow lifecycle queries only: neither returns a Runner or Task.
        self._scheduled_task_present = scheduled_task_present
        self._capture_wake = capture_wake

    def _scheduled_task_is_present(self) -> bool:
        query = self._scheduled_task_present
        return query() if query is not None else False

    def _wake_notification(self, *, tolerate_retired_loop=False):
        capture = self._capture_wake
        return capture(tolerate_retired_loop=tolerate_retired_loop) if capture is not None else None

    def begin_close(self, *, retire_wake) -> None:
        with self._protection_lock:
            self._closing = True
            retire_wake()
            self._scheduled_task_present = None
            self._capture_wake = None
        with self._unfilled_input_lock:
            self._unfilled_input = None
        if self._leader and self._strategy_inputs is not None:
            self._strategy_inputs.reset("runner_closed")
            self._strategy_inputs.reset_evaluations()

    async def finish_close(self, *, scheduled_task_was_present: bool) -> None:
        if not scheduled_task_was_present:
            def join_native_execution():
                with self._unfilled_preflight_lock:
                    pass
            await drain_thread_call(join_native_execution)
            if self._prepared and self._protection_snapshot() is not None:
                await drain_thread_call(self._save_checkpoint, force=True)
            return
        await drain_thread_call(self._save_checkpoint, force=True)

    async def record_iteration_error(self, error) -> None:
        reason = "RuntimeError" if isinstance(error, MockAutomationRiskSuperseded) else type(error).__name__
        self._status = {
            **(self._status if self._input_progress.recovering else {}),
            "state": "ERROR", "reason": reason,
            "detail": str(error), "orders_enabled": False,
            "updated_at": self._now().isoformat(),
        }
        try:
            await drain_thread_call(self._save_checkpoint)
        except Exception:
            logger.exception("automatic mock runner checkpoint failed")

    def next_poll_delay(self, maximum_seconds):
        timeout = maximum_seconds
        plan = self._unfilled_attempt_plan or self._unfilled_preflight
        due = self._cancel_recovery.get('next_check_at') if self._cancel_recovery is not None else plan.get('next_check_at') if plan else None
        if self._spec.unfilled_execution_policy is not None and due:
            seconds = (datetime.fromisoformat(due) - self._bundle.runtime.server_now()).total_seconds()
            if seconds > 0:
                timeout = min(timeout, seconds)
        elif self._unfilled_registration is not None:
            policy = self._validate_unfilled_registration(self._unfilled_registration)
            deadline = min(policy.wait_until, policy.protection_deadline)
            seconds = (deadline - self._bundle.runtime.server_now()).total_seconds()
            if seconds > 0:
                timeout = min(timeout, seconds)
        return timeout

    def prepare_restore(self) -> None:
        """Prepare native state before loop callbacks or publication can begin.

        The caller retains this Runner before dispatching the native operation.
        Close joins the existing execution lock, including failed preparation.
        """
        with self._unfilled_preflight_lock:
            if self._closing:
                raise RuntimeError("MOCK_AUTOMATION_RUNNER_CLOSED")
            if self._prepared:
                return
            self._prepare_restore_locked()
            self._prepared = True

    def _prepare_restore_locked(self) -> None:
        spec = self._spec
        if self._member_admission is not None:
            with self._repository.mock_automation_read_scope() as reader:
                args = {} if reader is None else {'_reader': reader}
                stored = self._repository.load_mock_automation_admission_for_spec(
                    self._bundle.account_ref, spec.spec_id, **args)
                control = self._repository.load_mock_automation_control(self._bundle.account_ref, **args)
                proposed = self._account_selection
                if proposed is not None:
                    selected = self._repository.load_mock_automation_account_selection(
                        self._bundle.account_ref, proposed.selection_id, **args)
                    if (selected != proposed or control is None
                            or control.execution_run_id != self._bundle.run_id
                            or control.active_spec_id != proposed.primary_spec_id
                            or not self._selection_matches_admission(proposed)):
                        raise ValueError('MOCK_AUTOMATION_PROPOSED_SELECTION_CHANGED')
                if stored != self._member_admission or proposed is None and not self._matches_control(control):
                    raise ValueError('MOCK_AUTOMATION_MEMBER_ADMISSION_CHANGED')
                self._account_selection = proposed or control.account_selection
                self._admitted_member_spec_ids = self._account_selection.member_spec_ids
        package = self._repository.load_mock_automation_candidate_package(
            spec.strategy_ref, spec.candidate_package_hash,
        )
        if package is None:
            raise ValueError("MOCK_AUTOMATION_CANDIDATE_MISSING")
        candidate = package.candidate_spec
        self._position_registered_implementation = package.scientific_implementation_hash
        from kiwoom_monitor.application.position_target import native_study_policy_implementation_ref
        try:self._position_policy_implementation = native_study_policy_implementation_ref()
        except (OSError,ValueError):self._position_policy_implementation = None
        family_id = str(candidate.get("family", ""))
        if self._minute_plan_mode:
            self._prepare_minute_plan(candidate)
            return
        self._family = get_research_family(family_id)
        self._config = parse_strategy_config(family_id, candidate.get("parameters", {}))
        self._leader = isinstance(self._config, LeaderBreakoutConfig)
        if self._leader_plan_mode and (not self._leader or not isinstance(self._bundle.repository, ExecutionRepository)):
            raise ValueError("native leader plan requires the existing leader execution owner")
        if hasattr(getattr(self._config, 'execution_contract', None), 'position_steps') and (
                not self._leader_plan_mode or spec.unfilled_execution_policy is None
                or spec.unfilled_execution_policy.accepted_order_management is None):
            raise ValueError('staged leader execution requires the explicit native current-order policy')
        if spec.session_profile != str(candidate.get("session_profile", {}).get("profile", "")):
            raise ValueError("MOCK_AUTOMATION_SESSION_PROFILE_MISMATCH")
        if spec.unfilled_execution_policy is not None:
            from kiwoom_monitor.application.research_execution import SimulationCostModel
            execution = candidate.get('execution_model')
            raw = execution.get('cost_model') if isinstance(execution,Mapping) else None
            if raw is not None:
                try:
                    self._unfilled_cost_model = SimulationCostModel(**raw)
                except (ValueError,TypeError):
                    pass  # Invalid/missing modeled cost never blocks protective management.
        self._strategy_inputs = LeaderTradeInputs() if self._leader else MinuteStrategyInputs(
            session_profile=str(self._spec.session_profile),
            keep_bars=int(self._config.lookback_bars) + 2,
            universe_horizon=max(int(self._spec.maximum_data_gap_seconds or 0),
                int(self._config.rank_window_seconds or 0) + int(self._config.rank_max_gap_seconds or 0)),
        )
        runner_document = self._checkpoint_document("execution_mock_automation_runner_current")
        restored_clock = None
        if self._config.session_exit_policy:
            clock = self._checkpoint_document(CLOCK_COLLECTION)
            if isinstance(clock, Mapping) and (
                clock.get("spec_id") == self._spec.spec_id
                and clock.get("execution_run_id") == self._bundle.run_id
                and clock.get("candidate_package_hash") == self._spec.candidate_package_hash
            ):
                if clock.get("version") != SESSION_CLOCK_VERSION:
                    raise RuntimeError("unsupported_mock_session_clock_version")
                if (type(clock.get("control_revision")) is not int
                        or clock["control_revision"] <= 0
                        or not isinstance(clock.get("cancel_attempted_intent_ids"), list)
                        or any(not isinstance(value, str) or not value
                               for value in clock["cancel_attempted_intent_ids"])):
                    raise ValueError("invalid_mock_session_clock_receipt")
                restored_clock = dict(clock)
        self._restore_or_bootstrap(runner_document)
        self._session_clock = restored_clock

    def _prepare_minute_plan(self, candidate):
        from kiwoom_monitor.application.section12_minute_research import MinuteExperiment
        from kiwoom_monitor.domain.research_contract import MINUTE_FINAL_CANDIDATE_FAMILIES
        from .minute_plan_management import MinutePlanManagement
        from kiwoom_monitor.domain.execution_activation import CLOSE_INPUT_PROTOCOL, PANIC_INPUT_PROTOCOL
        protocol = PANIC_INPUT_PROTOCOL if self._panic_plan_mode else CLOSE_INPUT_PROTOCOL
        management_key = "minute_management" if self._panic_plan_mode else "close_management"

        if candidate.get('family') not in MINUTE_FINAL_CANDIDATE_FAMILIES or not isinstance(self._bundle.repository, ExecutionRepository):
            raise ValueError('native minute plan requires the existing execution owner')
        self._config = MinuteExperiment.from_document(candidate['family'], candidate['parameters'])
        self._strategy_inputs = MinuteStrategyInputs(session_profile=self._spec.session_profile,
            keep_bars=(self._config.policy.lookback_minutes if self._panic_plan_mode else self._config.policy.lookback_bars) + 2, universe_horizon=self._spec.maximum_data_gap_seconds)
        self._input_progress.use_native_protocol(protocol)
        self._minute_management = MinutePlanManagement(self._store, self._bundle, self._spec, self._repository,
            parameters=candidate['parameters'], price_source=self._price_source, now_provider=self._now,
            family=candidate['family'], candidate_current=self._minute_candidate_current, close_calendar=self._close_calendar,
            nxt_price_source=self._nxt_price_source, observe_entry_market=self._observe_entry_market,
            prepare_entry_market=self._prepare_entry_market)
        self._episode_position.set_plan_fill_listener(self._minute_management.observe_fill)
        document = self._checkpoint_document('execution_mock_automation_runner_current')
        matching = isinstance(document, Mapping) and (
            document.get('version'), document.get('spec_id'), document.get('execution_run_id'), document.get('candidate_package_hash')
        ) == (RUNNER_CHECKPOINT_VERSION, self._spec.spec_id, self._bundle.run_id, self._spec.candidate_package_hash)
        if matching:
            if document.get('delivery_protocol') != protocol:
                raise ValueError('unsupported CLOSE input protocol')
            self._episode_position.restore_fill_cursor(document.get('fill_cursor', 0))
            self._episode_position.restore_strategy_state(document.get('strategy_state', {}))
            self._episode_position.restore_seen_fill_ids(document.get('seen_fill_ids', ()))
            self._checkpoint.restore_revision(document.get('checkpoint_revision', 0))
        self._minute_management.prepare(document.get(management_key) if matching else None)
        notice = document.get('protection_review') if matching else None
        if notice is not None:
            self._validate_protection_review(notice)
            self._protection_review = deepcopy(notice)
        self._episode_position.apply_new_fills(self._bundle.repository, leader=False, config=self._config,
                                               position_time=self._position_state_time)
        self._status = dict(self._minute_management.status)
        self._save_checkpoint()



    @property
    def account_ref(self) -> str:
        return self._bundle.account_ref

    @property
    def credential_profile_id(self) -> str:
        return self._bundle.credential_profile_id

    def is_bound_to(self, bundle: Any) -> bool:
        """Compare the borrowed bundle identity, without claiming its lease."""
        return self._bundle is bundle

    def shares_account_membership(self, other) -> bool:
        """Compare admitted members without lending another Runtime or its state."""
        return (isinstance(other, MockAutomationEpisode) and self._member_admission is not None
                and other._member_admission is not None and self._bundle is other._bundle
                and self._account_selection is not None and self._account_selection == other._account_selection)

    def position_shadow_observation(self, base, *, is_current: Callable[[], bool]):
        """Enrich an account-only cut from this runner's existing RAM owners.

        The supervisor supplies its registry/closing fence; this owner checks
        the monitor epoch between copies. Neither fence grants order authority.
        """
        from kiwoom_monitor.application.position_target import (
            PositionAccountObservation, PositionRunnerAccountObservation,
            PositionStudyRunnerAccountObservation,
        )
        matches = getattr(getattr(self._bundle, "monitor", None), "position_observation_matches", None)
        if not callable(matches) or not matches(base):
            return base
        state = self.position_study_state(base)
        if not is_current() or state is None or not matches(base):
            return base
        getter = getattr(self._bundle.monitor, "position_study_portfolio", None)
        portfolio = getter(base) if callable(getter) else None
        if not is_current() or not matches(base):
            return base
        if isinstance(portfolio, dict) and portfolio.get("version") == "native-position-study-portfolio/v1":
            policy_getter = getattr(self, "position_study_policy", None)
            policy = policy_getter(base, state) if callable(policy_getter) else None
            if not is_current() or not matches(base):
                return base
            return PositionStudyRunnerAccountObservation(
                **{field.name: getattr(base, field.name) for field in fields(PositionAccountObservation)},
                runner_state=state, study_portfolio=portfolio, native_study_policy=policy,
            )
        return PositionRunnerAccountObservation(
            **{field.name: getattr(base, field.name) for field in fields(PositionAccountObservation)},
            runner_state=state,
        )

    def held_close_decisions(self):
        if self._minute_management is None:
            return ()
        return self._minute_management.held_close_decisions()

    def _prepare_entry_market(self, cut):
        if self._market_comparison is None:
            return
        try:
            comparison = self._market_comparison(cut)
        except Exception:
            logger.warning("아침 시황의 장중 대조 근거를 읽지 못했습니다", exc_info=True)
            comparison = {"status": "UNCONFIRMED", "as_of": cut.isoformat(),
                          "reason": "comparison_unavailable", "advisory_only": True}
        return comparison

    def _observe_entry_market(self, symbol, comparison):
        if comparison is not None:
            self._entry_market_comparison = {**comparison, "symbol": symbol}

    @property
    def status(self) -> dict[str, Any]:
        value = {
            "version": "mock_automation_runner_status/v1",
            "account_ref": self._bundle.account_ref,
            "spec_id": self._spec.spec_id,
            "execution_run_id": self._bundle.run_id,
            "input_cursor": self._input_progress.cursor,
            "fill_cursor": self._episode_position.fill_cursor,
            "strategy_state": self._episode_position.state.status,
            **self._status,
            "latency": dict(self._latency) if self._latency is not None else None,
        }
        if self._entry_market_comparison is not None:
            value["entry_market_comparison"] = deepcopy(self._entry_market_comparison)
        if self._input_progress.recovering:
            value["input_recovery"] = self._input_progress.recovery_receipt()
            if self._status.get("state") not in ("BLOCKED", "STOPPED", "ERROR"):
                value.update(state="WARMUP", reason="legacy_frames_recovery", orders_enabled=False)
        if self._session_clock is not None:
            value["session_clock"] = deepcopy(self._session_clock)
        if self._unfilled_preflight is not None:
            value["unfilled_management_preflight"] = {
                "receipt": deepcopy(self._unfilled_preflight),
                "requires_revalidation": True, "order_authorized": False,
            }
            value["unfilled_management_clock"] = session_clock_assessment(
                self._config, self._episode_position.state, self._bundle.runtime.server_now(),
                management_preflight=self._unfilled_attempt_plan or self._unfilled_preflight,
            )
        if self._unfilled_attempt_plan is not None:
            value['unfilled_execution_preflight'] = {
                'receipt': deepcopy(self._unfilled_attempt_plan), 'requires_revalidation': True,
                'order_authorized': False,
            }
        with self._unfilled_input_lock:
            if self._unfilled_delivery is not None:
                value['unfilled_input_delivery'] = deepcopy(self._unfilled_delivery)
                value['unfilled_input_delivery']['pending'] = self._unfilled_input is not None
            if self._unfilled_population and self._unfilled_population.get('source_publication'):
                value['unfilled_population_source']=deepcopy(self._unfilled_population['source_publication'])
                value['unfilled_population_source']['available_at']=self._unfilled_population['available_at'].isoformat()
        if self._unfilled_source_status is not None:
            value['unfilled_native_source'] = deepcopy(self._unfilled_source_status)
        if self._unfilled_registration is not None:
            value['unfilled_registration'] = deepcopy(self._unfilled_registration)
        if self._cancel_recovery is not None:
            value['protective_cancel_recovery'] = deepcopy(self._cancel_recovery)
        if self._protection_exit is not None:
            value['protection_exit'] = deepcopy(self._protection_exit)
        notice = self._protection_snapshot()
        if self._native_risk_delivery is not None:
            value["native_risk_delivery"] = deepcopy(self._native_risk_delivery)
        if notice is not None:
            value["protection_review"] = notice
            if notice["state"] == "PENDING":
                value["orders_enabled"] = False
                value["protection_review_required"] = True
        return value

    def position_study_policy(self, observation, runner_state):
        """Copy frozen policy in the same borrowed epoch; no credentials or IO."""
        from kiwoom_monitor.application.mock_automation_risk import _sha256
        if (self._position_policy_implementation is None or self._spec.unfilled_execution_policy is None
                or self.position_study_state(observation)!=runner_state):return None
        policy={'version':'native-policy-paper/v1',
            **{key:getattr(self._spec,key) for key in ('maximum_daily_loss_won','maximum_data_gap_seconds',
                'maximum_submission_unknown_count','maximum_reconnect_count','maximum_balance_mismatch_count')},
            'unfilled_policy':self._spec.unfilled_execution_policy.to_dict(),
            'implementation_ref':self._position_policy_implementation}
        body={'version':'native-position-study-policy/v1','source_kind':'owner_ram',
            'account_input_ref':observation.input_ref,'risk_snapshot_ref':observation.risk_snapshot.snapshot_id,
            'state_ref':runner_state['state_ref'],'policy':policy,'order_authorized':False}
        body['policy_observation_ref']='native-position-study-policy:'+_sha256(body)
        return body if self.position_study_state(observation)==runner_state else None

    def position_study_state(self, observation):
        """Copy existing state without waiting for fills, locks, DB or protection."""
        from kiwoom_monitor.application.position_target import validate_position_runner_state
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_expected_binding_revision
        if (self._closing or self._input_progress.bootstrap_pending or self._input_progress.recovering
                or self._bundle.runtime.observation_in_progress):
            return None
        position_cut = self._episode_position.try_observation_cut()
        if position_cut is None:
            return None
        revision, state, cursor, available = position_cut
        if not self._protection_lock.acquire(blocking=False):return None
        try:notice=deepcopy(self._protection_review)
        finally:self._protection_lock.release()
        runtime_revision=self._bundle.runtime.observation_revision
        try:
            clock=deepcopy(self._session_clock)
            risk=observation.risk_snapshot
            if notice is not None:self._validate_protection_review(notice)
            if (risk is None or observation.account.account_ref!=self._bundle.account_ref
                    or risk.execution_run_id!=self._bundle.run_id or risk.binding_revision!=mock_automation_expected_binding_revision(
                        self._spec, self._selected_management_context)
                    or risk.data_path!=self._spec.data_path or not observation.reconciliation_complete
                    or not observation.open_orders_complete or observation.reasons):return None
            body={'version':'native-position-runner-state/v1','source_kind':'owner_ram',
                'account_ref':self._bundle.account_ref,'execution_run_id':self._bundle.run_id,
                'binding_revision':risk.binding_revision,'data_path':self._spec.data_path,
                'account_input_ref':observation.input_ref,'risk_snapshot_ref':risk.snapshot_id,
                'account_observed_at':observation.observed_at.isoformat(),'state_available_at':available.isoformat(),
                'state_revision':revision,'runtime_observation_revision':runtime_revision,'fill_cursor':cursor,
                'risk_event_high_watermark':risk.execution_event_high_watermark,
                'family':self._config.family if self._minute_plan_mode else self._family.family_id,
                'parameters':self._config.to_dict(),'session_profile':self._spec.session_profile,
                'registered_implementation_hash':self._position_registered_implementation,
                'candidate_package_hash':self._spec.candidate_package_hash,'strategy_state':state.to_dict(),
                'protection_review':notice,'session_clock':clock,
                'native_risk_limits':{key:getattr(self._spec,key) for key in ('maximum_daily_loss_won',
                    'maximum_data_gap_seconds','maximum_submission_unknown_count','maximum_reconnect_count','maximum_balance_mismatch_count')},
                'order_authorized':False,'source_durability':'not_verified'}
            body=json.loads(json.dumps(body,ensure_ascii=False,allow_nan=False))
            body['state_ref']='native-position-runner-state:'+hashlib.sha256(json.dumps(body,
                sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            validate_position_runner_state(body,observation)
            if (self._closing or self._input_progress.bootstrap_pending or self._input_progress.recovering
                    or self._session_clock!=clock or self._bundle.runtime.observation_in_progress
                    or runtime_revision!=self._bundle.runtime.observation_revision
                    or not self._episode_position.observation_cut_matches(position_cut)):return None
            if not self._protection_lock.acquire(blocking=False):return None
            try:
                if self._protection_review!=notice:return None
            finally:self._protection_lock.release()
            return body
        except (ValueError,TypeError,KeyError,RuntimeError):return None

    def _position_state_time(self):
        # Diagnostic availability cannot turn a successful fill into a failure.
        try:
            runtime=getattr(self._bundle,'runtime',None)
            value=runtime.server_now() if runtime is not None else None
            return value if isinstance(value,datetime) and value.tzinfo is not None else None
        except Exception:return None

    def _protection_snapshot(self):
        with self._protection_lock:
            return deepcopy(self._protection_review)

    def _protection_pending(self) -> bool:
        with self._protection_lock:
            return self._protection_review is not None and self._protection_review["state"] == "PENDING"

    def _validate_protection_review(self, notice) -> None:
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_expected_binding_revision
        if (not isinstance(notice, Mapping) or notice.get("version") != PROTECTION_REVIEW_VERSION
                or notice.get("account_ref") != self._bundle.account_ref
                or notice.get("execution_run_id") != self._bundle.run_id
                or notice.get("spec_id") != self._spec.spec_id
                or notice.get("state") not in {"PENDING", "RESOLVED_FLAT"}
                or notice.get("order_authorized") is not False or notice.get("transport_state") != "OFF"
                or type(notice.get("revision")) is not int or notice["revision"] <= 0
                or any(not isinstance(notice.get(key), str) or not 0 < len(notice[key]) <= 256
                       for key in ("reason", "input_ref"))):
            raise ValueError("invalid_mock_protection_review")
        first = datetime.fromisoformat(notice["first_requested_at"])
        latest = datetime.fromisoformat(notice["requested_at"])
        if first.tzinfo is None or latest.tzinfo is None or first > latest:
            raise ValueError("invalid_mock_protection_review_time")
        if notice["state"] == "RESOLVED_FLAT":
            if not notice.get("resolved_risk_snapshot_id") or type(notice.get("resolved_risk_revision")) is not int:
                raise ValueError("invalid_mock_protection_resolution")
        trigger = notice.get("risk_trigger")
        if trigger is not None:
            if (not isinstance(trigger, Mapping) or trigger.get("version") != "mock-native-risk-protection/v1"
                    or trigger.get("account_ref") != self._bundle.account_ref
                    or trigger.get("execution_run_id") != self._bundle.run_id
                    or trigger.get("binding_revision") != mock_automation_expected_binding_revision(
                        self._spec, self._protection_policy_context(trigger))
                    or trigger.get("disposition") not in {"EXIT_REQUIRED", "RECONCILE_REQUIRED"}
                    or type(trigger.get("reconciliation_revision")) is not int or trigger["reconciliation_revision"] <= 0
                    or any(not isinstance(trigger.get(key), str) or len(trigger[key]) != 64
                           for key in ("snapshot_id", "cause_signature"))
                    or not isinstance(trigger.get("reasons"), list) or not trigger["reasons"]
                    or any(not isinstance(reason, str) or not 0 < len(reason) <= 64 for reason in trigger["reasons"])
                    or trigger.get("source") != "acknowledged_account_monitor"):
                raise ValueError("invalid_mock_native_risk_trigger")
            if datetime.fromisoformat(trigger["observed_at"]).tzinfo is None:
                raise ValueError("invalid_mock_native_risk_trigger_time")
            if 'stop_loss' in trigger:
                from kiwoom_monitor.application.mock_automation_risk import validate_mock_stop_loss_proof
                validate_mock_stop_loss_proof(trigger['stop_loss'], self._spec, **self._stop_validation_terms())
        if "leader_stop_observation" in notice:
            proof = (trigger or {}).get("stop_loss")
            if not self._leader or not isinstance(proof, Mapping) or self._leader_position_proof is None:
                raise ValueError("leader_stop_observation_requires_pinned_stop")
            context = validate_leader_position_proof(
                self._leader_position_proof, self._config, self._spec, self._bundle.run_id,
            )
            raw = replay_leader_stop_observation(self._config, context, notice["leader_stop_observation"])
            if (raw["status"], raw["input_ref"], raw["signal_reference_price"], raw["available_at"]) != (
                "HARD_EXIT", proof["input_ref"], proof["price_won"], proof["available_at"],
            ):
                raise ValueError("leader_stop_observation_does_not_reproduce")

    def observe_native_risk(self, cut) -> None:
        if self._closing:
            return
        risk, account = cut["risk"], cut["account"]
        if (risk.account_as_of != account.as_of
                or account.account_ref != self._bundle.account_ref):
            raise ValueError("mock_protection_native_recovery_mismatch")
        if self._member_admission is not None:
            risk.owned_for_member(self._spec.spec_id)
            risk.entry_prices_for_member(self._spec.spec_id)
            risk.pending_sells_for_member(self._spec.spec_id)
        self._stop_loss_cut = (risk, dict(account.positions))
        quote = self._strategy_inputs.latest_stop_quote if self._leader else None
        if quote is not None:
            self._observe_leader_stop(quote)
        now = self._bundle.runtime.server_now()
        trigger = self._native_protection_trigger(risk, now=now, account_positions=account.positions)
        self._native_risk_delivery = {"snapshot_id": risk.snapshot_id,
            "reconciliation_revision": risk.reconciliation_revision,
            "delivered_at": now.isoformat(), "triggered": trigger is not None}
        if self._protection_pending() and self._protection_exit_recovery_revision is not None:
            notify = self._wake_notification()
            if notify is not None:
                notify()
        if self._unfilled_registration is not None:
            facts = self._native_unfilled_facts(risk)
            if (facts != self._unfilled_source_facts or self._unfilled_source_recovery_deadline is not None
                    and self._unfilled_source_deadline != self._unfilled_source_recovery_deadline
                    or self._unfilled_source_pending and (self._unfilled_source_status or {}).get('reason') in {
                        'native_account_cut_unavailable','awaiting_aligned_native_decision_cut',
                        'native_account_cut_incomplete_or_expired'}):
                self._unfilled_source_facts = facts
                self._unfilled_source_generation += 1
                self._unfilled_source_pending = True
                notify = self._wake_notification()
                if notify is not None:
                    notify()
        if (self._spec.unfilled_execution_policy is not None
                and trigger is None):
            previous_stop = (self._protection_snapshot() or {}).get('risk_trigger', {}).get('stop_loss')
            if previous_stop is not None:
                price = {'symbol':previous_stop['symbol'], 'price_won':previous_stop['price_won'],
                    'available_at':datetime.fromisoformat(previous_stop['available_at']),
                    'input_ref':previous_stop['input_ref'], 'durable':False, 'market_status':'TRADABLE',
                    **({'venue': previous_stop['venue']} if 'venue' in previous_stop else {})}
                self.observe_automation_price(previous_stop['symbol'], price)
            else:
                for symbol, _ in risk.entry_prices_for_spec(self._spec.spec_id):
                    price = self._price_source(symbol) if self._price_source is not None else None
                    if price is not None:
                        self.observe_automation_price(symbol, price)
        if trigger is None:
            return  # Healthy recovery cannot erase an armed protection request.
        previous = self._protection_snapshot()
        stop = (previous or {}).get('risk_trigger', {}).get('stop_loss')
        if self._minute_plan_mode and stop is not None and previous['state'] == 'PENDING':
            return  # Keep the original historical cut; the current cut still updates above.
        if stop is not None and trigger['disposition'] == 'RECONCILE_REQUIRED':
            trigger = {**trigger, 'stop_loss':stop, 'reasons':[*trigger['reasons'], 'SYMBOL_STOP_LOSS_REACHED']}
        if (trigger['disposition'] == 'EXIT_REQUIRED' and previous is not None
                and previous.get('risk_trigger',{}).get('disposition') == 'EXIT_REQUIRED'
                and 'stop_loss' in previous.get('risk_trigger',{})):
            return
        if (previous is not None and previous["state"] == "PENDING"
                and previous.get("risk_trigger", {}).get("cause_signature") == trigger["cause_signature"]):
            return
        self.request_protection_review(reason="native_risk:" + ";".join(trigger["reasons"]),
            input_ref="native-risk:" + risk.snapshot_id, requested_at=now, risk_trigger=trigger)

    def _native_protection_trigger(self, risk, *, now, account_positions=None):
        return mock_automation_protection_trigger(self._spec, risk,
            execution_run_id=self._bundle.run_id, now=now, account_positions=account_positions,
            selected_management_context=self._selected_management_context)

    def _stop_validation_terms(self):
        if self._panic_plan_mode:
            return {}  # PANIC absolute setup triggers are owned by the retained minute plan.
        if self._minute_plan_mode:
            return {'stop_loss_bps': self._config.policy.protection_loss_bps, 'ipo_policy': self._config.policy,
                    'ipo_candidate': self._minute_management.active_candidate()}
        if self._leader:
            return {"leader_config": self._config, "position_proof": self._leader_position_proof}
        return {"stop_loss_bps": self._config.stop_loss_bps}

    def observe_leader_trade(self, payload, received_at, *, source, approved):
        """Bounded RAM delivery only; DB and submission stay in the owned iteration."""
        if not self._leader or self._closing:
            return
        envelope = self._strategy_inputs.offer(payload, received_at, source=source, approved=approved)
        if envelope is None:
            return
        if self._leader_position_proof is not None:
            try:
                context = validate_leader_position_proof(self._leader_position_proof, self._config, self._spec, self._bundle.run_id)
                quote = self._strategy_inputs.capture_stop_quote(self._config, envelope,
                    decision_time=self._now(), position_context=context)
                if quote is not None:
                    self._observe_leader_stop(quote)
            except (TypeError, ValueError, KeyError):
                self._strategy_inputs.clear_stop_quote(envelope)
        notify = self._wake_notification()
        if notify is not None:
            notify()

    def _observe_leader_stop(self, quote):
        raw = quote.raw
        if (self._closing or self._spec.unfilled_execution_policy is None or self._stop_loss_cut is None
                or self._leader_position_proof is None or raw.get("status") != "HARD_EXIT"
                or quote is not self._strategy_inputs.latest_stop_quote):
            return
        risk, positions = self._stop_loss_cut
        if self._leader_plan_mode and (not risk.reconciliation_complete or risk.submission_unknown_count
                                      or risk.balance_mismatch_count):
            return  # The historical cut needs exact owned exposure before it can arm a typed stop.
        context = validate_leader_position_proof(self._leader_position_proof, self._config, self._spec, self._bundle.run_id)
        symbol = context.symbol
        quantity = dict(risk.owned_for_spec(self._spec.spec_id)).get(symbol)
        entry = dict(risk.entry_prices_for_spec(self._spec.spec_id)).get(symbol)
        now = self._now()
        at = datetime.fromisoformat(raw["available_at"])
        staged = hasattr(self._config.execution_contract, 'position_steps')
        position_matches = (positions.get(symbol, 0) >= quantity if (staged or self._member_admission is not None) and quantity else positions.get(symbol) == quantity)
        if (not quantity or not entry or not position_matches
                or at < risk.account_as_of or not 0 <= (now-at).total_seconds() <= self._config.max_data_age_seconds):
            return
        previous = self._protection_snapshot()
        if previous is not None and previous.get("risk_trigger", {}).get("stop_loss") is not None:
            return  # The original approved stop quote is sticky until reconciliation.
        proof = {"version": "leader_absolute_stop/v1", "symbol": symbol, "quantity": quantity,
            "entry_price_won": entry, "price_won": raw["signal_reference_price"], "available_at": raw["available_at"],
            "input_ref": raw["input_ref"], "hard_stop_price": context.hard_stop_price,
            "position_proof": deepcopy(self._leader_position_proof), "rules_hash": self._config.rules_hash,
            "package_hash": self._spec.candidate_package_hash}
        from kiwoom_monitor.application.mock_automation_risk import validate_mock_stop_loss_proof, _sha256
        validate_mock_stop_loss_proof(proof, self._spec, risk, **self._stop_validation_terms())
        trigger = self._native_protection_trigger(risk,
            now=now, account_positions=positions)
        if trigger is None:
            trigger = {"version": "mock-native-risk-protection/v1", "account_ref": risk.account_ref,
                "execution_run_id": self._bundle.run_id, "binding_revision": risk.binding_revision,
                "snapshot_id": risk.snapshot_id, "reconciliation_revision": risk.reconciliation_revision,
                "observed_at": risk.observed_at.isoformat(), "disposition": "EXIT_REQUIRED", "reasons": [],
                "source": "acknowledged_account_monitor"}
        trigger = {**trigger, "reasons": [*trigger["reasons"], "SYMBOL_STOP_LOSS_REACHED"], "stop_loss": proof}
        trigger["cause_signature"] = _sha256({"stop_loss": proof, "disposition": trigger["disposition"], "reasons": trigger["reasons"]})
        self._record_protection_review(reason="native_risk:leader_absolute_stop", input_ref=raw["input_ref"],
            requested_at=now, risk_trigger=trigger, leader_stop_quote=quote)

    async def _run_leader_page(self, control, limit):
        """Actual 0B -> the shared kernel -> existing FILL/Risk/claim/dispatch."""
        discontinuity = self._strategy_inputs.take_discontinuity()
        if discontinuity:
            self._status = {"state": "WARMUP", "reason": discontinuity, "orders_enabled": False, "input_mode": "0B"}
        if self._episode_position.state.status == "open" and self._leader_position_proof is None:
            self._status = {"state": "BLOCKED", "reason": "owned_position_context_unavailable", "orders_enabled": False}
            await drain_thread_call(self._save_checkpoint)
            return 0
        consumed = 0
        entry_market = None
        market_prepared = False
        while consumed < min(limit, 1000):
            envelope = self._strategy_inputs.take()
            if envelope is None:
                break
            payload, received_at, source = envelope.payload, envelope.received_at, envelope.source
            consumed += 1
            now = self._now()
            try:
                if not market_prepared and self._episode_position.state.status != "open":
                    entry_market = self._prepare_entry_market(now)
                    market_prepared = True
                current = self._current_leader_context()
                config = self._strategy_inputs.resolve_config(self._config, current,
                    position_open=self._episode_position.state.status == "open")
                context = validate_leader_position_proof(self._leader_position_proof, self._config, self._spec,
                    self._bundle.run_id) if self._episode_position.state.status == "open" else None
                symbol = str(payload.get("code", ""))
                if self._episode_position.state.status == "open" and symbol != self._episode_position.state.symbol:
                    continue
                raw, context = self._strategy_inputs.evaluate_trade(config, payload, received_at=received_at,
                    source=source, decision_time=now, position_open=self._episode_position.state.status == "open", position_context=context)
                assessment = session_clock_assessment(config, self._episode_position.state, now)
                if assessment["exit_required"] and raw["status"] in {"HOLD", "HARD_EXIT", "INVALIDATED"}:
                    raw = {**raw, "proposal": "EXIT", "reasons": [*raw["reasons"], *assessment["reasons"]]}
                if hasattr(config.execution_contract, 'position_steps') and context is not None:
                    enabled = (control.desired_state.value == 'RUNNING' and not control.entry_resume_required and self._allows_entry(control)
                        and isinstance(current, Mapping) and leader_current_entry_matches(self._config,
                            self._leader_position_proof, {'shadow_candidate_enabled': current.get('enabled'),
                                'shadow_candidate_config': current.get('policy')}, now))
                    raw = self._strategy_inputs.evaluate_position_step(config, raw, self._episode_position.state,
                        context, source=source, entry_enabled=enabled)
                evaluation = leader_evaluation(run_id=self._bundle.run_id, config=config, raw=raw, state=self._episode_position.state, context=context)
                if evaluation.decision.final_action == 'ENTER' and not self._allows_entry(control):
                    self._status = {'state': 'MANAGING', 'reason': 'retained_member_entry_disabled', 'orders_enabled': False}
                    continue
                active = await drain_thread_call(self._bundle.repository.active_intents, "mock", self._bundle.account_ref, self._bundle.run_id)
                active = self._own_active_records(active)
                if active:
                    if evaluation.decision.final_action == "EXIT":
                        self.request_protection_review(reason="registered_strategy_protective_exit", input_ref=raw["input_ref"], requested_at=now)
                    self._status = {"state": "WAITING_ORDER", "reason": "active_intent_reconciliation", "orders_enabled": False}
                    continue
                position_action = evaluation.decision.final_action in {'ADD', 'REDUCE'}
                if not position_action:
                    self._episode_position.replace_strategy_state(evaluation.state, position_time=self._position_state_time)
                decision = evaluation.decision
                if decision.final_action not in {"ENTER", "EXIT", 'ADD', 'REDUCE'}:
                    self._status = {"state": "BLOCKED" if decision.constraints or raw["status"] == "BLOCKED" else "RUNNING",
                        "reason": ";".join(decision.reasons), "last_decision_id": decision.decision_id, "input_mode": "0B", "orders_enabled": False}
                    continue
                recovery, risk = await self._refresh_recovery_risk()
                if risk is None or not self._episode_position.matches_risk(risk):
                    raise ValueError("strategy_state_risk_mismatch")
                await drain_thread_call(self._record_recovery, recovery, risk)
                if await self._pause_for_protection_review():
                    break
                if decision.final_action == "ENTER":
                    self._observe_entry_market(decision.symbol, entry_market)
                    proof = leader_position_proof(config, spec_id=self._spec.spec_id, package_hash=self._spec.candidate_package_hash,
                        account_ref=self._bundle.account_ref, run_id=self._bundle.run_id, decision=decision, context=context)
                    validate_leader_position_proof(proof, self._config, self._spec, self._bundle.run_id)
                    self._leader_position_proof = proof
                    await drain_thread_call(self._save_checkpoint, force=True)
                    await drain_thread_call(self._store.upsert_documents, "execution_leader_position_context",
                        [{"owner": self._bundle.account_ref, "key": proof["entry_intent_id"], "document": proof}])
                await drain_thread_call(self._store.upsert_documents, "execution_leader_decisions",
                    [{"owner": self._bundle.account_ref, "key": self._leader_evidence_key(decision.decision_id), "document": {
                        "snapshot": evaluation.snapshot.to_dict(), "decision": decision.to_dict(),
                        "observation": {"payload": payload, "received_at": received_at.isoformat(), "source": source, "approved": True},
                         **({"market_comparison": self._entry_market_comparison}
                            if decision.final_action == "ENTER" and self._entry_market_comparison is not None else {}),
                        "operational_revision": current.get("revision") if isinstance(current, Mapping) else None,
                        "policy_hash": config.policy.fingerprint,
                        "policy": config.policy.to_dict(), "position_proof": self._leader_position_proof}}])
                plan = await drain_thread_call(self._leader_plan_for_decision, decision) if self._leader_plan_mode else None
                gate, record, _ = await drain_thread_call(
                    self._dispatch_original_and_register, decision, recovery, risk, plan=plan,
                    **({'position_action_ref': leader_position_action_ref(config, self._leader_position_proof, raw)}
                       if position_action else {}),
                )
                if position_action and record is not None:
                    self._episode_position.replace_strategy_state(evaluation.state, position_time=self._position_state_time)
                    await drain_thread_call(self._save_checkpoint, force=True)
                submitted = record is not None and record.state is not OrderState.REJECTED
                self._status = {"state": "ORDER_SUBMITTED" if submitted else "BLOCKED",
                    "reason": ";".join(gate.reasons) if gate.reasons else "approved" if submitted else "order_rejected",
                    "last_decision_id": decision.decision_id,
                    "pending_intent_id": record.intent.intent_id if record is not None else "", "input_mode": "0B", "orders_enabled": False}
            except (TypeError, ValueError, KeyError) as error:
                self._strategy_inputs.reset_evaluations()
                self._status = {"state": "BLOCKED", "reason": str(error), "input_mode": "0B", "orders_enabled": False}
            await drain_thread_call(self._save_checkpoint)
        if discontinuity and not consumed:
            await drain_thread_call(self._save_checkpoint)
        return consumed

    def observe_automation_price(self, symbol, price):
        """RAM-only immediate stop cue; transport remains in the existing Runner."""
        if price.get('venue', 'KRX') != ('NXT' if self._spec.supported_venue == 'NXT' else 'KRX'):
            return
        if self._panic_plan_mode:
            if (not self._closing and self._minute_management is not None
                    and self._minute_management.observe_price(symbol, price)):
                notify = self._wake_notification()
                if notify is not None: notify()
            return
        if self._leader:
            return  # The approved actual-0B callback owns the new observation proof.
        if (self._closing or self._stop_loss_cut is None
                or self._spec.unfilled_execution_policy is None and not self._minute_plan_mode):
            return
        risk, positions = self._stop_loss_cut
        if symbol not in dict(risk.entry_prices_for_spec(self._spec.spec_id)):
            return
        previous = self._protection_snapshot()
        if not (previous or {}).get('risk_trigger', {}).get('stop_loss') and price['available_at'] < risk.observed_at:
            return  # An old pre-fill quote cannot initiate a new native stop.
        if previous is not None and previous['state'] == 'PENDING':
            previous_trigger = previous.get('risk_trigger', {})
            # A confirmed exit and its first quote are sticky throughout the episode.
            if previous_trigger.get('disposition') == 'EXIT_REQUIRED':
                return
            proof = previous_trigger.get('stop_loss')
            if proof is not None:
                price = {'price_won':proof['price_won'], 'available_at':datetime.fromisoformat(proof['available_at']),
                    'input_ref':proof['input_ref'], 'durable':False, 'market_status':'TRADABLE',
                    **({'venue': proof['venue']} if 'venue' in proof else {})}
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_stop_loss_trigger
        now = self._bundle.runtime.server_now()
        trigger = mock_automation_stop_loss_trigger(self._spec, risk, execution_run_id=self._bundle.run_id,
            now=now, price={**price, 'symbol':symbol}, account_positions=positions,
            selected_management_context=self._selected_management_context,
            **self._stop_validation_terms())
        if trigger is None or previous is not None and previous.get('risk_trigger', {}).get('cause_signature') == trigger['cause_signature']:
            return
        self.request_protection_review(reason='native_risk:'+';'.join(trigger['reasons']),
            input_ref=price['input_ref'], requested_at=now, risk_trigger=trigger)
        if trigger['disposition'] == 'RECONCILE_REQUIRED' and not (previous or {}).get('risk_trigger', {}).get('stop_loss'):
            self._bundle.monitor.request_automation_execution_recovery()

    def request_protection_review(self, *, reason: str, input_ref: str, requested_at: datetime,
                                  risk_trigger=None) -> dict[str, Any]:
        """Bounded RAM cue for the existing loop, with no DB or transport work.

        This OFF seam does not interrupt a native operation already in progress.
        It remains armed until fresh native flat/reconciled evidence resolves it.
        """
        return self._record_protection_review(reason=reason, input_ref=input_ref,
                                              requested_at=requested_at, risk_trigger=risk_trigger)

    def _record_protection_review(self, *, reason: str, input_ref: str, requested_at: datetime,
                                  risk_trigger=None, draining_cancel: bool = False,
                                  leader_stop_quote=None) -> dict[str, Any] | None:
        now = self._bundle.runtime.server_now()
        if requested_at.tzinfo is None or requested_at > now:
            raise ValueError("invalid_mock_protection_review_time")
        with self._protection_lock:
            if self._closing and not draining_cancel:
                raise RuntimeError("MOCK_AUTOMATION_RUNNER_CLOSED")
            previous = self._protection_review
            if (self._leader_plan_mode and previous is not None and previous["state"] == "PENDING"
                    and "planned_leader_stop" in previous):
                return deepcopy(previous)  # The same stop/notice must match any durable cancel receipt.
            # Select the historical stop while its source is still current.
            # A later source reset can retire future quotes, but not this stop.
            if leader_stop_quote is not None and leader_stop_quote is not self._strategy_inputs.latest_stop_quote:
                return deepcopy(previous)
            leader_stop_observation = leader_stop_quote.observation() if leader_stop_quote is not None else None
            identity = (reason, input_ref, requested_at.isoformat())
            if previous is not None and previous["state"] == "PENDING" and identity == (
                    previous["reason"], previous["input_ref"], previous["requested_at"]):
                return deepcopy(previous)
            if previous is not None and previous["state"] == "PENDING" and requested_at < datetime.fromisoformat(previous["requested_at"]):
                raise ValueError("mock_protection_review_out_of_order")
            notice = {
                "version": PROTECTION_REVIEW_VERSION, "account_ref": self._bundle.account_ref,
                "execution_run_id": self._bundle.run_id, "spec_id": self._spec.spec_id,
                "revision": previous["revision"] + 1 if previous else 1,
                "state": "PENDING", "reason": reason, "input_ref": input_ref,
                "first_requested_at": previous["first_requested_at"] if previous and previous["state"] == "PENDING" else requested_at.isoformat(),
                "requested_at": requested_at.isoformat(), "transport_state": "OFF", "order_authorized": False,
            }
            if risk_trigger is not None:
                notice["risk_trigger"] = deepcopy(risk_trigger)
                if (leader_stop_observation is None and previous is not None and previous["state"] == "PENDING"
                        and "leader_stop_observation" in previous
                        and risk_trigger.get("stop_loss") == previous.get("risk_trigger", {}).get("stop_loss")):
                    leader_stop_observation = previous["leader_stop_observation"]
            if leader_stop_observation is not None:
                notice["leader_stop_observation"] = deepcopy(leader_stop_observation)
                if self._leader_plan_mode:
                    from kiwoom_monitor.application.mock_automation_risk import PlannedLeaderStop

                    proof = risk_trigger["stop_loss"]
                    pin = proof["position_proof"]
                    notice["planned_leader_stop"] = PlannedLeaderStop(
                        pin["entry_intent_id"], pin["proof_id"], risk_trigger["snapshot_id"],
                        risk_trigger["reconciliation_revision"], leader_stop_observation, proof,
                    ).to_dict()
            self._validate_protection_review(notice)
            self._protection_review = notice
            notify = None if self._closing else self._wake_notification()
        if notify is not None:
            notify()
        return deepcopy(notice)

    async def _pause_for_protection_review(self) -> bool:
        if not self._protection_pending():
            return False
        if not self._leader_plan_mode:
            await self._poll_first_protection_exit()
        self._status = {**self._status, "state": "PROTECTION_REVIEW_REQUIRED", "reason": "protection_review_pending",
                        "orders_enabled": False}
        await drain_thread_call(self._save_checkpoint)
        return True

    async def _manage_leader_plan(self, control, root):
        """Use the existing account owner for the selected plan; legacy management stays separate."""
        if root is None:
            return await self._pause_for_protection_review()
        now = self._now()
        notice = self._protection_snapshot() or {}
        stop = notice.get("planned_leader_stop") if notice.get("state") == "PENDING" else None
        gateway = getattr(self._bundle, "gateway", None)
        execution = self._bundle.repository
        if await self._manage_current_leader_order(control, root, notice):
            return True
        if root.state is OrderState.QUEUED and now >= root.intent.expires_at:
            await drain_thread_call(self._bundle.runtime.cancel, root.intent.intent_id)
        if gateway is not None and root.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
            await self._cancel_leader_plan_root(control, root, notice, now, gateway, execution)
        if stop and root.state in {OrderState.CANCELLED, OrderState.FILLED} and root.detailed_filled_quantity:
            await self._submit_leader_plan_protection(control, root, notice, now, execution)
        if await self._pause_for_protection_review():
            return True
        if root.state in TERMINAL_ORDER_STATES:
            return False
        status = {"state": "WAITING_ORDER", "reason": root.state.value, "orders_enabled": False,
                  "pending_intent_id": root.intent.intent_id, "input_mode": "0B"}
        if status != self._status or self._checkpoint.pending:
            self._status = status
            await drain_thread_call(self._save_checkpoint)
        return True

    async def _manage_current_leader_order(self, control, root, notice):
        """Selected current management uses the existing command owner and native claim."""
        terms = self._spec.unfilled_execution_policy
        if (root is None or terms is None or terms.accepted_order_management is None
                or self._bundle.gateway is None):
            return False
        execution = self._bundle.repository
        revision = self._bundle.runtime.observation_revision
        if self._leader_management_records is None or self._leader_management_records[0] != revision:
            active = await drain_thread_call(execution.active_intents, 'mock', self._bundle.account_ref, self._bundle.run_id)
            self._leader_management_records = (revision, self._own_active_records(active))
        active = self._leader_management_records[1]
        if not active:
            return False
        if (len(active) != 1 or not isinstance(active[0].intent, PlannedOrderIntent)
                or (active[0].intent.plan.plan_id, active[0].intent.plan.policy_ref)
                != (root.intent.plan.plan_id, root.intent.plan.policy_ref)):
            self._status = {'state': 'BLOCKED', 'reason': 'leader_current_order_account_coverage', 'orders_enabled': False}
            return True
        record = active[0]
        if record.state is OrderState.QUEUED:
            if hasattr(self._config.execution_contract, 'position_steps') and self._now() >= record.intent.expires_at:
                await drain_thread_call(self._bundle.runtime.cancel, record.intent.intent_id)
                self._status = {'state': 'WAITING_ORDER', 'reason': 'unsent_position_action_expired',
                                'orders_enabled': False, 'pending_intent_id': record.intent.intent_id, 'input_mode': '0B'}
                await drain_thread_call(self._save_checkpoint)
                return True
            return False  # The original unsent TTL/claim path still owns this order.
        if record.state is OrderState.SUBMISSION_UNKNOWN:
            self._bundle.monitor.request_automation_execution_recovery()
            self._status = {'state': 'WAITING_ORDER', 'reason': record.state.value, 'orders_enabled': False,
                            'pending_intent_id': record.intent.intent_id, 'input_mode': '0B'}
            return True
        if record.state is OrderState.CANCEL_PENDING:
            self._bundle.monitor.request_automation_execution_recovery()
        current = self._current_leader_context()
        quote = self._strategy_inputs.latest_stop_quote
        if quote is not None and not self._strategy_inputs.stop_quote_generation_is_current(quote):
            quote = None
        from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_venue
        route_quote = self._nxt_price_source(record.intent.symbol) if execution_broker_venue(record) == 'NXT' else None
        now = self._now()
        deadline = min(datetime.fromisoformat(self._leader_position_proof['context']['valid_until']),
                       record.intent.created_at + timedelta(seconds=terms.maximum_management_seconds))
        token = (revision, record.intent.intent_id, control.control_revision,
                 current.get('revision') if isinstance(current, Mapping) else None,
                 quote.raw['input_ref'] if quote is not None else None,
                 route_quote['input_ref'] if route_quote is not None else None,
                 quote is not None and 0 <= (now - quote.envelope.received_at).total_seconds() <= terms.max_snapshot_age_seconds,
                 now >= deadline, notice.get('revision') if notice.get('state') == 'PENDING' else None)
        if self._leader_management_input == token:
            return True
        protection = notice if notice.get('state') == 'PENDING' and 'planned_leader_stop' in notice else None
        if protection is not None:
            await drain_thread_call(self._save_checkpoint, force=True)
        outcome = {}
        def prepare(updated, cut):
            from kiwoom_monitor.application.leader_breakout import leader_current_entry_matches
            from kiwoom_monitor.application.leader_order_management import prepare_leader_order_management_claim
            settings = (self._store.load_document('server_operational_settings', 'global', 'current')
                        if updated.intent.side is OrderSide.BUY else None)
            enabled = (control.desired_state.value == 'RUNNING' and not control.entry_resume_required and self._allows_entry(control)
                and updated.intent.side is OrderSide.BUY and leader_current_entry_matches(self._config,
                    self._leader_position_proof, settings['document'] if settings else None, self._now()))
            current_quote = self._strategy_inputs.latest_stop_quote
            if current_quote is not None and not self._strategy_inputs.stop_quote_generation_is_current(current_quote):
                current_quote = None
            if hasattr(self._config.execution_contract, 'position_steps'):
                self._read_leader_plan_root(execution)
            claim = prepare_leader_order_management_claim(self._store, self._bundle.runtime, execution, self._spec,
                updated, config=self._config, quote=current_quote, recovery=cut['recovery'], risk=cut['risk'],
                entry_enabled=enabled, protection_notice=protection,
                route_quote=(self._nxt_price_source(updated.intent.symbol)
                             if execution_broker_venue(updated) == 'NXT' else None),
                verified_peer_orders=cut.get('verified_peer_orders'),
                selected_management_context=self._selected_management_context,
                selected_management_policy=(self._repository.selected_management_policy
                    if self._selected_management_context is not None else None),
                plan_entry_notional_won=(self._leader_staged_facts['entry_notional']
                    if self._leader_staged_facts is not None else None))
            if claim is not None:
                outcome.update(claim.assessment)
            return claim
        try:
            updated = await self._bundle.gateway.manage_close_order(record, spec_id=self._spec.spec_id,
                control_revision=control.control_revision, prepare_management=prepare)
        except (ValueError, RuntimeError) as error:
            self._status = {'state': 'BLOCKED', 'reason': str(error), 'orders_enabled': False,
                            'pending_intent_id': record.intent.intent_id, 'input_mode': '0B'}
            await drain_thread_call(self._save_checkpoint)
            return True
        # Account refresh itself can advance observation_revision. A WAIT must
        # acknowledge that resulting cut rather than immediately refresh it again.
        self._leader_management_input = ((self._bundle.runtime.observation_revision, *token[1:])
            if outcome.get('action') not in {'AMEND', 'CANCEL'} else None)
        self._status = {'state': 'WAITING_ORDER', 'reason': outcome.get('reason', updated.state.value),
                        'orders_enabled': False, 'pending_intent_id': updated.intent.intent_id, 'input_mode': '0B'}
        await drain_thread_call(self._save_checkpoint)
        return True

    async def _cancel_leader_plan_root(self, control, root, notice, now, gateway, execution):
        from kiwoom_monitor.application.mock_automation_risk import PlannedLeaderStopCancelRequest

        stop = notice.get("planned_leader_stop")
        expiry = root.state is OrderState.ACCEPTED or stop is None
        if expiry and (self._leader_plan_deadline is None or now < self._leader_plan_deadline):
            return
        key = (root.intent.intent_id, notice.get("revision") if not expiry else None)
        if key == self._leader_plan_cancel_attempt:
            return
        head = await drain_thread_call(execution.load_plan_position, self._bundle.account_ref, self._bundle.run_id,
                                       root.intent.symbol, root.intent.plan)
        if not expiry:
            await drain_thread_call(self._save_checkpoint, force=True)
        terms = ({"expired_entry_plan": head.plan} if expiry else
                 {"planned_stop_cancel_request": PlannedLeaderStopCancelRequest(head.plan, stop["trigger_id"], notice["revision"])})
        try:
            await gateway.cancel_automation_intent(root, spec_id=self._spec.spec_id,
                                                   control_revision=control.control_revision, **terms)
        except (ValueError, RuntimeError) as error:
            self._status["planned_cancel_reason"] = str(error)
            return
        self._leader_plan_cancel_attempt = key

    async def _submit_leader_plan_protection(self, control, root, notice, now, execution):
        from kiwoom_monitor.application.mock_automation_risk import (
            prepare_planned_leader_protection_exit, read_planned_leader_protection_checkpoint,
        )

        monitor = self._bundle.monitor
        cut = getattr(monitor, "latest_automation_execution_cut", None)
        quote = self._leader_plan_refresh_quote
        if quote is not None:
            max_age = min(self._config.max_data_age_seconds or 0, self._spec.maximum_data_gap_seconds or 0,
                          self._spec.unfilled_execution_policy.max_snapshot_age_seconds)
            if (not self._strategy_inputs.stop_quote_generation_is_current(quote)
                    or not 0 <= (now - datetime.fromisoformat(quote.raw["observed_at"])).total_seconds() <= max_age
                    or not 0 <= (now - quote.envelope.received_at).total_seconds() <= max_age):
                self._leader_plan_refresh_quote = quote = None
        if quote is None:
            quote = self._strategy_inputs.latest_stop_quote
        stop = notice["planned_leader_stop"]["stop_loss"]
        confirmed_at = (self._leader_plan_confirmation[1] if self._spec.unfilled_execution_policy.accepted_order_management is not None
                        and self._leader_plan_confirmation is not None else root.last_broker_as_of)
        if (quote is None or quote.envelope.received_at <= datetime.fromisoformat(stop["available_at"])
                or confirmed_at is None or quote.envelope.received_at < confirmed_at):
            return
        if hasattr(self._config.execution_contract, 'position_steps') and (
                self._leader_staged_facts is None or self._leader_staged_facts['confirmed_at'] is None
                or quote.envelope.received_at < self._leader_staged_facts['confirmed_at']):
            return
        route_quote = self._price_source(root.intent.symbol) if root.intent.venue == 'NXT' else None
        if root.intent.venue == 'NXT' and route_quote is None:
            return
        required_at = max(quote.envelope.received_at, route_quote['available_at']) if route_quote is not None else quote.envelope.received_at
        if cut is None or cut["risk"].observed_at < required_at:
            if self._leader_plan_refresh_quote is None:
                # Keep this cut while Risk catches up, even during continuous 0B delivery.
                self._leader_plan_refresh_quote = quote
                monitor.request_automation_execution_recovery()
            return
        observation = cut["observation"]
        if (not observation.reconciliation_complete or not observation.open_orders_complete
                or observation.unmapped_broker_order_ids or self._own_active_records(observation.open_orders)):
            return
        risk, recovery = cut["risk"], cut["recovery"]
        head = await drain_thread_call(execution.load_plan_position, self._bundle.account_ref, self._bundle.run_id,
                                       root.intent.symbol, root.intent.plan)
        try:
            workflow = prepare_planned_leader_protection_exit(
                self._spec, run_id=self._bundle.run_id, control_revision=control.control_revision, notice=notice,
                plan=head.plan, observation=quote.observation(), risk=risk,
                plan_position={"version": "mock_plan_position/v1", "plan_id": head.plan.plan_id,
                               "policy_ref": head.plan.policy_ref, "revision": head.plan.expected_revision,
                               "run_id": self._bundle.run_id, "symbol": head.symbol, "quantity": head.quantity,
                               "fill_cursor": head.fill_cursor, "unreconciled_sell_quantity": head.unreconciled_sell_quantity,
                               **({'spec_id': self._spec.spec_id} if self._member_admission is not None
                                  or self._selected_management_context is not None
                                  and self._selected_management_context.member_spec_id is not None else {})},
                leader_config=self._config, position_proof=self._leader_position_proof,
                confirmed_root_state=root.state.value, confirmed_root_at=confirmed_at, now=now,
                selected_management_context=self._selected_management_context,
                route_quote=route_quote,
            )
            plan, decision = read_planned_leader_protection_checkpoint(workflow, notice, self._spec, self._bundle.run_id)
            await drain_thread_call(self._record_recovery, recovery, risk)
            with self._protection_lock:
                if (self._protection_review != notice
                        or not self._strategy_inputs.stop_quote_generation_is_current(quote)):
                    return
                self._protection_review = {**notice, "planned_leader_protection_exit": workflow}
                self._planned_protection_restore = (workflow, plan, decision)
                self._leader_plan_refresh_quote = None
            await drain_thread_call(self._save_checkpoint, force=True)
            gate, record, _ = await drain_thread_call(self._dispatch_original_and_register, decision, recovery, risk,
                                    plan=plan, protection_exit_id=workflow["workflow_id"])
            if record is not None:
                self._status.update(pending_intent_id=record.intent.intent_id, last_decision_id=decision.decision_id)
            elif gate.reasons:
                self._status['planned_protection_reason'] = ';'.join(gate.reasons)
        except (ValueError, RuntimeError) as error:
            self._status["planned_protection_reason"] = str(error)

    async def _poll_first_protection_exit(self):
        if self._spec.unfilled_execution_policy is None:
            return
        monitor=getattr(self._bundle,'monitor',None)
        if not getattr(monitor,'automation_execution_inputs_enabled',False):
            return
        notice=self._protection_snapshot()
        if self._protection_exit is not None and self._unfilled_registration is None and not self._protection_exit_recovery_checked:
            await drain_thread_call(self._restore_first_protection_root)
            self._protection_exit_recovery_checked=True
        if notice.get('risk_trigger',{}).get('disposition') != 'EXIT_REQUIRED':
            return  # Caller cues and uncertain native Risk retain review-only semantics.
        cut=monitor.latest_automation_execution_cut
        now=self._bundle.runtime.server_now()
        age=min(self._spec.maximum_data_gap_seconds,self._spec.unfilled_execution_policy.max_snapshot_age_seconds)
        if self._unfilled_registration and self._unfilled_registration['purpose']=='PROTECTIVE_EXIT':
            if (self._protection_exit is not None and cut is not None
                    and cut['risk'].reconciliation_complete and not cut['risk'].owned_position_quantities
                    and not cut['risk'].pending_sell_quantities and not cut['risk'].submission_unknown_count
                    and not cut['risk'].balance_mismatch_count and cut['observation'].reconciliation_complete
                    and cut['observation'].open_orders_complete and not cut['observation'].unmapped_broker_order_ids
                    and not cut['observation'].open_orders and not any(cut['observation'].account.positions.values())
                    and all(0 <= (now-at).total_seconds() <= age for at in (
                        cut['risk'].observed_at,cut['risk'].account_as_of,cut['observation'].observed_at))
                    and self._episode_position.matches_risk(cut['risk'])):
                if self._status.get('first_protection_exit_state') != 'LIQUIDATION_CONFIRMED':
                    self._status.update(first_protection_exit_state='LIQUIDATION_CONFIRMED',
                        protection_flat_risk_snapshot_id=cut['risk'].snapshot_id)
            return  # The existing unfilled path alone manages this original root/children.
        if self._protection_exit is not None:
            snapshot=self._protection_exit['snapshot']
            price={'input_ref':snapshot['price_input_ref'],'price_won':snapshot['price_won'],
                'available_at':datetime.fromisoformat(snapshot['price_available_at']), 'durable':False,'market_status':'TRADABLE'}
        else:
            cached=self._protection_exit_price
            stop=notice.get('risk_trigger',{}).get('stop_loss')
            if stop is not None:
                cached=(notice['revision'], {'price_won':stop['price_won'],
                    'available_at':datetime.fromisoformat(stop['available_at']), 'input_ref':stop['input_ref'],
                    'durable':False, 'market_status':'TRADABLE'})
                self._protection_exit_price=cached
            if stop is None and (cached is None or cached[0]!=notice['revision'] or (now-cached[1]['available_at']).total_seconds()>age):
                candidate=self._price_source(self._episode_position.state.symbol) if self._price_source and self._episode_position.state.status=='open' else None
                self._protection_exit_price=(notice['revision'],candidate) if candidate else None
            price=self._protection_exit_price[1] if self._protection_exit_price else None
        ready=bool(price and price.get('durable') is False and price.get('market_status')=='TRADABLE'
            and type(price.get('price_won')) is int and price['price_won']>0
            and 0 <= (now-price['available_at']).total_seconds() <= age)
        fresh=bool(cut and all(0 <= (now-at).total_seconds() <= age
            for at in (cut['risk'].observed_at,cut['risk'].account_as_of,cut['observation'].observed_at)))
        aligned=bool(cut and ready and price['available_at'] <= cut['risk'].observed_at)
        facts=(notice['revision'],self._native_unfilled_facts(cut['risk']) if cut else None,ready,fresh,aligned)
        if facts==self._protection_exit_facts:
            return
        self._protection_exit_facts=facts
        if not fresh or not aligned:
            reason='protection_exit_price_unavailable' if not ready else 'protection_exit_current_account_cut_required'
            self._status.update(first_protection_exit_state='BLOCKED',first_protection_exit_reason=reason)
            if notice.get('risk_trigger',{}).get('stop_loss') is not None and not ready:
                self._status['requires_operator_attention']=True
            if ready and self._protection_exit_recovery_revision != notice['revision']:
                self._protection_exit_recovery_revision=notice['revision']
                monitor.request_automation_execution_recovery()
            return
        try:
            await drain_thread_call(self._start_first_protection_exit_locked,cut,notice,price)
        except Exception as error:
            self._status.update(first_protection_exit_state='BLOCKED',first_protection_exit_reason=str(error),
                                requires_operator_attention=True)

    def _start_first_protection_exit_locked(self, cut, notice, price):
        from kiwoom_monitor.application.mock_automation_risk import prepare_mock_protection_exit, validate_mock_protection_exit
        with self._bundle.runtime.automation_decision_guard():
            risk,observation,recovery=cut['risk'],cut['observation'],cut['recovery']
            def verify():
                now=self._bundle.runtime.server_now()
                age=min(self._spec.maximum_data_gap_seconds,self._spec.unfilled_execution_policy.max_snapshot_age_seconds)
                current=self._bundle.monitor.latest_automation_execution_cut
                control=self._repository.load_mock_automation_control(self._bundle.account_ref)
                trigger=self._native_protection_trigger(risk,now=now)
                if (self._closing or self._protection_snapshot()!=notice or current is None
                        or current['risk']!=risk or current['observation']!=observation
                        or self._repository.load_latest_mock_automation_risk(self._bundle.account_ref)!=risk
                        or control is None or control.desired_state.value!='RUNNING'
                        or control.active_spec_id!=self._spec.spec_id or control.execution_run_id!=self._bundle.run_id
                        or not risk.reconciliation_complete or risk.submission_unknown_count or risk.balance_mismatch_count
                        or risk.pending_sell_quantities or not observation.open_orders_complete
                        or not observation.reconciliation_complete or observation.unmapped_broker_order_ids
                        or observation.account!=recovery.account or observation.risk_snapshot!=risk
                        or observation.open_orders or any(row.state not in TERMINAL_ORDER_STATES for row in recovery.orders)
                        or not self._episode_position.matches_risk(risk) or self._episode_position.state.status!='open'
                        or observation.account.positions.get(self._episode_position.state.symbol)!=self._episode_position.state.position_quantity
                        or trigger is not None and trigger['disposition']!='EXIT_REQUIRED'
                        or any(not 0 <= (now-at).total_seconds() <= age for at in (
                            risk.observed_at,risk.account_as_of,observation.observed_at,price['available_at']))):
                    raise ValueError('protection_exit_native_cut_or_control_changed')
                return control
            self._episode_position.apply_new_fills(
                self._bundle.repository, leader=self._leader, config=self._config,
                position_time=self._position_state_time,
            )
            control=verify()
            if 'stop_loss' in notice.get('risk_trigger',{}):
                from kiwoom_monitor.application.mock_automation_risk import validate_mock_stop_loss_proof
                validate_mock_stop_loss_proof(notice['risk_trigger']['stop_loss'], self._spec, risk,
                    **self._stop_validation_terms())
            active,complete=self._bundle.repository.account_active_intents(self._bundle.account_ref)
            if self._own_active_records(active) or not complete:
                raise ValueError('protection_exit_account_order_in_flight_or_coverage_unknown')
            if self._unfilled_preflight is not None:
                ids=[self._unfilled_preflight['anchor']['root_intent']['intent_id']]
                children=self._unfilled_attempt_plan['children'] if self._unfilled_attempt_plan else []
                ids.extend(row['intent']['intent_id'] for row in children)
                previous=[self._bundle.runtime.load_intent(identity) for identity in ids]
                if any(row is None or row.state not in TERMINAL_ORDER_STATES
                       or row.filled_quantity!=row.detailed_filled_quantity for row in previous):
                    raise ValueError('protection_exit_prior_management_episode_unresolved')
            if self._protection_exit is None:
                self._protection_exit=prepare_mock_protection_exit(self._spec,run_id=self._bundle.run_id,
                    control_revision=control.control_revision,notice=notice,risk=risk,state=self._episode_position.state,price=price)
            decision=validate_mock_protection_exit(self._protection_exit,self._spec,self._bundle.run_id,
                selected_management_context=self._protection_policy_context(self._protection_exit['snapshot']['request_trigger']))
            snapshot=self._protection_exit['snapshot']
            if (snapshot['control_revision']!=control.control_revision or snapshot['request_revision']!=notice['revision']
                    or snapshot['request_input_ref']!=notice['input_ref'] or snapshot['request_trigger']!=notice['risk_trigger']
                    or decision.quantity!=self._episode_position.state.position_quantity
                    or self._bundle.runtime.server_now() >= datetime.fromisoformat(decision.decided_at)+timedelta(seconds=self._spec.maximum_data_gap_seconds)):
                raise ValueError('protection_exit_fixed_decision_changed_or_expired')
            self._status.update(first_protection_exit_state='PREPARED',first_protection_exit_reason='native_loss_protection')
            self._save_checkpoint()  # ACK the fixed decision before native QUEUED.
            verify()
            self._record_recovery(recovery, risk)
            gate,record,_=self._dispatch_original_and_register(decision,recovery,risk,
                protection_exit_id=self._protection_exit['workflow_id'])
            if record is None:
                raise ValueError('protection_exit_gate_blocked:'+';'.join(gate.reasons))
            self._status.update(first_protection_exit_state='ORDER_SUBMITTED',pending_intent_id=record.intent.intent_id,
                first_protection_exit_reason=record.state.value)
            self._save_checkpoint()

    def _restore_first_protection_root(self):
        from kiwoom_monitor.application.mock_automation_risk import validate_mock_protection_exit, PROTECTION_EXIT_VERSION
        from kiwoom_monitor.application.mock_automation_execution import mock_automation_intent_id
        decision=validate_mock_protection_exit(self._protection_exit,self._spec,self._bundle.run_id,
            selected_management_context=self._protection_policy_context(self._protection_exit['snapshot']['request_trigger']))
        identity=mock_automation_intent_id(self._spec.spec_id,decision.decision_id)
        with self._bundle.runtime.automation_decision_guard():
            record=self._bundle.runtime.load_intent(identity)
            if record is None:return
            claim=self._bundle.repository.protection_claim(identity)
            gate=self._repository.load_mock_automation_approved_gate(self._bundle.account_ref,identity)
            if (claim is None or claim.get('workflow_id')!=self._protection_exit['workflow_id'] or gate is None
                    or claim.get('gate_id')!=gate.gate_id or claim.get('risk_snapshot_id')!=gate.risk_snapshot_id
                    or claim.get('risk_reconciliation_revision')!=gate.risk_reconciliation_revision
                    or record.intent.policy_version!=PROTECTION_EXIT_VERSION or record.intent.side is not OrderSide.SELL
                    or record.intent.symbol!=decision.symbol or record.intent.quantity!=decision.quantity
                    or record.intent.limit_price!=decision.signal_reference_price
                    or record.intent.created_at!=datetime.fromisoformat(decision.decided_at)):
                raise ValueError('protection_exit_native_claim_recovery_mismatch')
            self._register_native_unfilled(record,gate,decision)
            self._status.update(first_protection_exit_state='NATIVE_RESTORED',pending_intent_id=identity,
                first_protection_exit_reason=record.state.value)
            self._save_checkpoint()

    def resolve_protection_review(self, *, expected_revision: int, recovery, risk, planned_resume_control=None) -> dict[str, Any]:
        """Explicit OFF review completion, only with current fully flat native proof."""
        from kiwoom_monitor.application.mock_automation_risk import is_planned_zero_fill_reconciliation_review

        if self._closing or self._scheduled_task_is_present() or not self._unfilled_preflight_lock.acquire(blocking=False):
            raise RuntimeError("mock_protection_resolution_requires_idle_runner")
        try:
            notice = self._protection_snapshot()
            if notice is None or notice["state"] != "PENDING" or notice["revision"] != expected_revision:
                raise ValueError("mock_protection_review_revision_changed")
            with self._bundle.runtime.automation_decision_guard():
                control = self._repository.load_mock_automation_control(self._bundle.account_ref)
                resolving_before_resume = planned_resume_control is not None
                zero_fill_review = self._leader_plan_mode and is_planned_zero_fill_reconciliation_review(notice)
                typed_review = self._planned_protection_restore is not None or zero_fill_review
                if self._leader_plan_mode and typed_review and not resolving_before_resume:
                    raise ValueError("planned_protection_resolution_requires_explicit_resume")
                control_allows_resolution = (
                    (self._leader_plan_mode and typed_review or self._member_admission is not None)
                    and control == planned_resume_control
                    and (control.desired_state.value == "STOPPED" or control.entry_resume_required)
                    if resolving_before_resume else control is not None and control.desired_state.value == "RUNNING"
                )
                current = self._repository.load_latest_mock_automation_risk(self._bundle.account_ref)
                admission = self._repository.load_mock_automation_admission_for_spec(self._bundle.account_ref, self._spec.spec_id)
                linked = self._repository.load_latest_mock_automation_recovery(self._bundle.account_ref, admission.admission_id) if admission else None
                active = self._bundle.repository.account_active_intents(self._bundle.account_ref)
                member_resolution = self._member_admission is not None
                if member_resolution:
                    from kiwoom_monitor.application.mock_automation_recovery import MockAutomationAccountRecoveryDecision, MockAutomationRecoveryStatus
                    if (not isinstance(linked, MockAutomationAccountRecoveryDecision)
                            or control is None or control.account_selection is None
                            or linked.account_selection_id != control.account_selection.selection_id
                            or linked.member_spec_ids != control.account_selection.member_spec_ids
                            or linked.status is MockAutomationRecoveryStatus.BLOCKED):
                        raise ValueError('mock_protection_resolution_member_recovery_unverified')
                peer_orders = set(linked.other_member_open_order_ids) if member_resolution else set()
                owned = risk.owned_for_member(self._spec.spec_id) if member_resolution else risk.owned_position_quantities
                pending = risk.pending_sells_for_member(self._spec.spec_id) if member_resolution else risk.pending_sell_quantities
                now = self._bundle.runtime.server_now()
                if (current != risk or control is None or not self._matches_control(control)
                        or control.execution_run_id != self._bundle.run_id or not control_allows_resolution
                        or linked is None or linked.risk_snapshot_id != risk.snapshot_id
                        or linked.risk_reconciliation_revision != risk.reconciliation_revision
                        or risk.account_ref != self._bundle.account_ref or risk.execution_run_id != self._bundle.run_id
                        or risk.account_as_of != recovery.account.as_of or not risk.reconciliation_complete
                        or recovery.account.account_ref != self._bundle.account_ref
                        or (linked.own_position_quantities if member_resolution else recovery.account.positions)
                        or any(order.state not in TERMINAL_ORDER_STATES and order.broker_order_id not in peer_orders
                               for order in recovery.orders)
                        or owned or pending or risk.submission_unknown_count
                        or risk.balance_mismatch_count or not active[1] or self._own_active_records(active[0])
                        or self._spec.maximum_data_gap_seconds is None
                        or risk.broker_query_started_at < datetime.fromisoformat(notice["requested_at"])
                        or not 0 <= (now - risk.observed_at).total_seconds() <= self._spec.maximum_data_gap_seconds
                        or not 0 <= (now - risk.account_as_of).total_seconds() <= self._spec.maximum_data_gap_seconds):
                    raise ValueError("mock_protection_resolution_native_flat_unverified")
                self._episode_position.apply_new_fills(
                    self._bundle.repository, leader=self._leader, config=self._config,
                    position_time=self._position_state_time,
                )
                if not self._episode_position.matches_risk(risk):
                    raise ValueError("mock_protection_resolution_native_flat_unverified")
                if self._planned_protection_restore is not None:
                    self._read_planned_protection(self._bundle.repository)
                    readback = self._planned_protection_readback
                    projection = readback[1] if readback is not None else {}
                    if (projection.get("state") != "FILLED" or any(projection.get(name) != 0 for name in (
                        "plan_quantity", "reserved_quantity", "unreconciled_sell_quantity",
                    ))):
                        raise ValueError("planned_protection_resolution_terminal_fill_unverified")
                zero_fill_receipt = self._read_zero_fill_plan_receipt(recovery) if zero_fill_review else None
                # Keep the request visible throughout native I/O. A newer cue
                # wins; resolution cannot overwrite it while saving its ACK.
                with self._protection_lock:
                    if self._protection_review != notice:
                        raise ValueError("mock_protection_review_revision_changed")
                    resolved = {**notice, "state": "RESOLVED_FLAT", "resolved_risk_snapshot_id": risk.snapshot_id,
                                "resolved_risk_revision": risk.reconciliation_revision}
                    if resolving_before_resume:
                        if zero_fill_receipt is not None:
                            resolved["planned_zero_fill_root"] = zero_fill_receipt
                        resolved["resume_from_control_revision"] = control.control_revision
                        if self._leader_plan_mode and typed_review:
                            from kiwoom_monitor.application.mock_automation_risk import planned_review_resume_id
                            resolved["planned_review_resume_id"] = planned_review_resume_id(resolved)
                self._save_checkpoint(protection_review=resolved)
                if (self._repository.load_latest_mock_automation_risk(self._bundle.account_ref) != risk
                        or self._repository.load_mock_automation_control(self._bundle.account_ref) != control
                        or self._bundle.repository.account_active_intents(self._bundle.account_ref) != active
                        or self._protection_snapshot() != notice):
                    raise ValueError("mock_protection_resolution_native_state_changed")
                self._bundle.runtime.heartbeat()
                after_ack = self._bundle.runtime.server_now()
                if any(not 0 <= (after_ack - cut).total_seconds() <= self._spec.maximum_data_gap_seconds
                       for cut in (risk.observed_at, risk.account_as_of)):
                    raise ValueError("mock_protection_resolution_cut_expired_during_ack")
                with self._protection_lock:
                    if self._protection_review != notice:
                        raise ValueError("mock_protection_review_revision_changed")
                    self._protection_review = resolved
                return deepcopy(resolved)
        except BaseException:
            # Failed ACK/revalidation never disarms the RAM protection cue.
            if 'notice' in locals() and notice is not None:
                with self._protection_lock:
                    if self._protection_review is not None and self._protection_review["revision"] == notice["revision"]:
                        self._protection_review = notice
            raise
        finally:
            self._unfilled_preflight_lock.release()

    def preview_unfilled_execution(self, **inputs):
        """Explicit idle preview; the running owner uses the same locked core."""
        if not self._unfilled_preflight_lock.acquire(blocking=False):
            raise RuntimeError('unfilled_preflight_already_in_progress')
        try:
            if self._closing or self._scheduled_task_is_present() or self._input_progress.bootstrap_pending or self._input_progress.recovering:
                raise RuntimeError('unfilled_preflight_requires_idle_ready_runner')
            return self._preview_unfilled_execution_locked(**inputs)
        finally:
            self._unfilled_preflight_lock.release()

    def _preview_unfilled_execution_locked(self, *, policy, broker_snapshot, target, observation, assessment, recovery, risk):
        if self._unfilled_preflight is None:
            raise ValueError('unfilled_native_plan_requires_preserved_anchor')
        validate_unfilled_management_preflight(self._unfilled_preflight)
        from kiwoom_monitor.application.position_target import _json_value
        anchor = self._unfilled_preflight['anchor']
        encoded_policy = json.loads(json.dumps(asdict(policy), default=_json_value))
        if anchor['policy'] != encoded_policy or policy.maximum_reprices > 64:
            raise ValueError('unfilled_native_plan_policy_changed_or_budget_unsupported')
        execution = self._bundle.repository
        read_scope = (self._repository.mock_automation_read_scope
                      if isinstance(execution, ExecutionRepository) and execution.uses_store(self._store)
                      else nullcontext)
        with self._bundle.runtime.automation_decision_guard():
            with read_scope() as reader:
                reader_args = {} if reader is None else {'_reader': reader}
                control = self._repository.load_mock_automation_control(self._bundle.account_ref, **reader_args)
                if (control is None or control.desired_state.value != 'RUNNING'
                        or not self._matches_control(control)
                        or control.execution_run_id != self._bundle.run_id
                        or self._unfilled_registration is None
                            and control.control_revision != anchor['scope']['control_revision']):
                    raise ValueError('unfilled_native_plan_control_changed')
                if (observation.account != recovery.account or observation.risk_snapshot != risk
                        or observation.reconciliation_complete != risk.reconciliation_complete
                           and not self._flat_unfilled_buy_cancel_cut(policy, observation, risk)
                        or not observation.open_orders_complete
                        or observation.unmapped_broker_order_ids):
                    raise ValueError('unfilled_native_plan_account_or_risk_input_mismatch')
                now = self._bundle.runtime.server_now()
                if not 0 <= (now - target.decision_time).total_seconds() <= policy.max_snapshot_age_seconds:
                    raise ValueError('unfilled_preflight_cut_stale_or_future')
                root = execution.load(policy.anchor_intent_id, **reader_args)
                if root is None:
                    raise ValueError('unfilled_native_root_missing')
                if self._unfilled_registration is not None:
                    unfilled_management_control_revision(self._repository, self._spec, control, anchor['scope'],
                        policy, root, self._unfilled_registration,
                        admission_id=self._admission_id
                            if control.control_revision != anchor['scope']['control_revision'] else '', **reader_args)
                # Keep each FILL and lineage query fresh within this native read scope.
                self._episode_position.apply_new_fills(
                    execution, leader=self._leader, config=self._config,
                    position_time=self._position_state_time, **reader_args,
                )
                if not self._episode_position.matches_risk(risk) or self._episode_position.fill_cursor > risk.execution_event_high_watermark:
                    raise ValueError('unfilled_native_owned_state_or_risk_cut_mismatch')
                plan = deepcopy(self._unfilled_attempt_plan) if self._unfilled_attempt_plan is not None else seal_unfilled_attempt_plan({
                    'version': UNFILLED_ATTEMPT_PLAN_VERSION, 'anchor': deepcopy(anchor), 'children': [],
                    'native_claimed_intent_ids': [], 'native_submitted_intent_ids': [],
                    'result': deepcopy(self._unfilled_preflight['result']),
                    'next_check_at': self._unfilled_preflight['next_check_at'], 'order_authorized': False,
                })
                if plan['anchor'] != anchor:
                    raise ValueError('unfilled_native_plan_anchor_changed')
                marker = self._unfilled_preflight['reserved_child_intent_id']
                if marker is not None and execution.load(marker, **reader_args) is not None:
                    raise ValueError('unfilled_v1_preview_marker_in_native_ledger')
                next_child_id = 'unfilled-native-child:' + hashlib.sha256(
                    f'{root.intent.intent_id}:{len(plan["children"]) + 1}'.encode()).hexdigest()
                if execution.load(next_child_id, **reader_args) is not None:
                    raise ValueError('unfilled_native_child_without_preserved_manifest')
                records = [execution.load(slot['intent']['intent_id'], **reader_args) for slot in plan['children']]
                events = [execution.events(slot['intent']['intent_id'], **reader_args) for slot in plan['children']]
                root_events = execution.events(root.intent.intent_id, **reader_args)
            projection = project_unfilled_native_attempts(plan, root, records, events, root_events=root_events)
            managed = projection['managed_record']
            derived_assessment = replace(assessment, reprices_used=projection['native_reprices_used'])
            result = evaluate_unfilled_order_management_shadow(
                policy, managed, broker_snapshot, target, observation, derived_assessment, root_record=root,
            )
            body = {
                'run_id': self._bundle.run_id, 'snapshot_id': result['decision_id'],
                'symbol': managed.intent.symbol, 'proposal': target.action, 'final_action': target.action,
                'quantity': result['replacement_quantity'] or 0,
                'signal_reference_price': assessment.candidate_limit_price_won or managed.intent.limit_price,
                'required_capital_won': (result['replacement_quantity'] or 0) *
                    (assessment.candidate_limit_price_won or managed.intent.limit_price) if managed.intent.side is OrderSide.BUY else 0,
                'reasons': tuple(result['reasons']), 'constraints': (UNFILLED_ATTEMPT_PLAN_VERSION,),
                'state_before': self._episode_position.state.to_dict(), 'state_after': self._episode_position.state.to_dict(),
                'decided_at': target.decision_time.isoformat(),
            }
            from kiwoom_monitor.application.mock_automation_session_clock import content_id
            decision = StrategyDecision(decision_id=content_id('decision', body), **body)
            if result['action'] == 'REPRICE' and all(record is not None for record in records):
                attempt = len(plan['children']) + 1
                if attempt > policy.maximum_reprices:
                    raise ValueError('unfilled_native_plan_retry_budget_exhausted')
                intent = OrderIntent(
                    next_child_id, self._bundle.run_id, decision.decision_id, self._bundle.account_ref,
                    'mock', managed.intent.symbol, 'KRX', managed.intent.side,
                    result['replacement_quantity'], OrderType.LIMIT, result['replacement_limit_price_won'],
                    target.decision_time, min(policy.valid_until, target.decision_time + timedelta(
                        seconds=max(1, min(self._spec.maximum_data_gap_seconds or 1,
                            self._spec.unfilled_execution_policy.max_snapshot_age_seconds
                            if self._spec.unfilled_execution_policy else self._spec.maximum_data_gap_seconds or 1)))), UNFILLED_ATTEMPT_PLAN_VERSION,
                )
                plan['children'].append({'attempt': attempt, 'root_intent_id': root.intent.intent_id,
                    'parent_intent_id': managed.intent.intent_id,
                    'intent': json.loads(json.dumps(asdict(intent), default=_json_value))})
            plan.update({key: projection[key] for key in ('native_claimed_intent_ids', 'native_submitted_intent_ids')})
            plan.update(result=result, next_check_at=result['next_check_at'],
                        native_reprices_used=projection['native_reprices_used'],
                        native_submission_attempts=projection['native_submission_attempts'],
                        transport_state='OFF', order_authorized=False,
                        native_owner_authority='guarded_preview_only')
            # Reservation payload/deadline are immutable even when a newer
            # assessment changes price/quantity or renews its Risk cut.
            pending = plan['children'][-1]['intent'] if len(plan['children']) > len(projection['native_claimed_intent_ids']) else None
            plan['pending_child_matches_current_cut'] = bool(pending and result['action'] == 'REPRICE'
                and pending['quantity'] == result['replacement_quantity']
                and pending['limit_price'] == result['replacement_limit_price_won']
                and now < datetime.fromisoformat(pending['expires_at']))
            plan['native_gate_scope'] = 'current_assessment_only_not_child_submission_approval'
            protection_notice = self._protection_snapshot()
            gate, replacement_gate = assess_mock_unfilled_gate_bundle_from_risk(
                self._repository, self._bundle.runtime, decision, recovery, risk,
                account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id, strategy_ref=self._spec.strategy_ref,
                plan=plan, root=root, managed=managed,
                protection_review_required=bool(protection_notice and protection_notice['state'] == 'PENDING'),
            )
            plan.update(native_gate=gate.to_dict(), replacement_gate=replacement_gate,
                        assessment_decision=decision.to_dict())
            plan = seal_unfilled_attempt_plan(plan); validate_unfilled_attempt_plan(plan)
            self._unfilled_attempt_plan = plan
            self._save_checkpoint()
            if self._closing or self._protection_snapshot() != protection_notice:
                raise ValueError('unfilled_native_plan_changed_during_checkpoint')
            with self._repository.mock_automation_read_scope() as reader:
                reader_args = {} if reader is None else {'_reader': reader}
                if (self._repository.load_mock_automation_control(self._bundle.account_ref, **reader_args) != control
                        or self._repository.load_latest_mock_automation_risk(self._bundle.account_ref, **reader_args) != risk):
                    raise ValueError('unfilled_native_plan_changed_during_checkpoint')
                # Borrow the reader only when both repositories share its native store.
                ledger_args = (reader_args if isinstance(execution, ExecutionRepository)
                               and execution.uses_store(self._store) else {})
                ledger_changed = (
                    execution.load(root.intent.intent_id, **ledger_args) != root
                    or execution.events(root.intent.intent_id, **ledger_args) != root_events
                    or any(execution.load(slot['intent']['intent_id'], **ledger_args) != record
                           for slot, record in zip(plan['children'], records + [None] * (len(plan['children']) - len(records))))
                    or any(execution.events(slot['intent']['intent_id'], **ledger_args) != history
                           for slot, history in zip(plan['children'], events + [()] * (len(plan['children']) - len(events))))
                )
            if ledger_changed:
                raise ValueError('unfilled_native_plan_changed_during_checkpoint')
            self._bundle.runtime.heartbeat()  # Recheck actual owner after ACK; never open the order gate.
            age_limit = min(policy.max_snapshot_age_seconds, self._spec.maximum_data_gap_seconds
                            if self._spec.maximum_data_gap_seconds is not None else policy.max_snapshot_age_seconds)
            after_ack = self._bundle.runtime.server_now()
            if any(not 0 <= (after_ack - cut).total_seconds() <= age_limit
                   for cut in (target.decision_time, risk.observed_at)):
                raise ValueError('unfilled_native_plan_cut_expired_during_ack')
            return deepcopy(plan)

    def execute_unfilled_replacement(self, **inputs):
        """Explicit idle dispatcher; a public call cannot compete with the loop."""
        if self._closing or self._scheduled_task_is_present():
            raise RuntimeError('unfilled_execution_requires_idle_acknowledged_runner')
        if not self._unfilled_preflight_lock.acquire(blocking=False):
            raise RuntimeError('unfilled_preflight_already_in_progress')
        try:
            if self._closing or self._scheduled_task_is_present():
                raise RuntimeError('unfilled_execution_requires_idle_acknowledged_runner')
            return self._execute_unfilled_replacement_locked(**inputs)
        finally:
            self._unfilled_preflight_lock.release()

    def _execute_unfilled_replacement_locked(self, **inputs):
        if self._closing or self._checkpoint.pending:
            raise RuntimeError('unfilled_execution_requires_acknowledged_runner')
        plan = deepcopy(self._unfilled_attempt_plan)
        last = self._bundle.runtime.load_intent(plan['children'][-1]['intent']['intent_id']) if plan and plan['children'] else None
        next_attempt = bool(last and last.state is OrderState.CANCELLED and inputs['broker_snapshot'] is not None
                            and inputs['broker_snapshot'].broker_order_id == last.broker_order_id)
        if last is None or next_attempt:
            plan = self._preview_unfilled_execution_locked(**inputs)
        return self._dispatch_unfilled_replacement_locked(inputs, plan)

    def _dispatch_unfilled_replacement_locked(self, inputs, plan):
        gate, record, receipt = dispatch_mock_unfilled_replacement_from_risk(
            self._repository, self._bundle.runtime, plan=plan, **inputs,
            protection_review_required=self._protection_pending,
            execution_cancelled=lambda: self._closing,
            registration=self._unfilled_registration,
        )
        if record is not None:
            self._status = {'state': 'ORDER_SUBMITTED', 'reason': 'unfilled_native_replacement',
                            'pending_intent_id': record.intent.intent_id, 'orders_enabled': False}
            self._save_checkpoint()
        return gate, record, receipt

    def preview_unfilled_management(self, **inputs):
        """Explicit idle preview; no executable approval or broker command."""
        if not self._unfilled_preflight_lock.acquire(blocking=False):
            raise RuntimeError("unfilled_preflight_already_in_progress")
        try:
            if self._closing or self._scheduled_task_is_present() or self._input_progress.bootstrap_pending or self._input_progress.recovering:
                raise RuntimeError("unfilled_preflight_requires_idle_ready_runner")
            return self._preview_unfilled_management_locked(**inputs)
        finally:
            self._unfilled_preflight_lock.release()

    def _preview_unfilled_management_locked(self, *, policy, broker_snapshot, target, observation, assessment):
        execution = self._bundle.repository
        read_scope = (self._repository.mock_automation_read_scope
                      if isinstance(execution, ExecutionRepository) and execution.uses_store(self._store)
                      else nullcontext)
        with read_scope() as reader:
            reader_args = {} if reader is None else {'_reader': reader}
            control = self._repository.load_mock_automation_control(self._bundle.account_ref, **reader_args)
            if (control is None or control.desired_state.value != "RUNNING"
                    or not self._matches_control(control)
                    or control.execution_run_id != self._bundle.run_id):
                raise ValueError("unfilled_preflight_control_not_running")
            record = execution.load(policy.anchor_intent_id, **reader_args)
            if record is None:
                raise ValueError("unfilled_preflight_native_parent_missing")
            now = self._bundle.runtime.server_now()
            if not 0 <= (now - target.decision_time).total_seconds() <= policy.max_snapshot_age_seconds:
                raise ValueError("unfilled_preflight_cut_stale_or_future")
            scope = {"account_ref": self._bundle.account_ref, "execution_run_id": self._bundle.run_id,
                     "spec_id": self._spec.spec_id, "candidate_package_hash": self._spec.candidate_package_hash,
                     "control_revision": control.control_revision}
            if self._unfilled_registration is not None:
                if self._unfilled_preflight is not None:
                    scope = dict(self._unfilled_preflight['anchor']['scope'])
                else:
                    scope['control_revision'] = self._unfilled_registration['control_revision']
                unfilled_management_control_revision(self._repository, self._spec, control, scope,
                    policy, record, self._unfilled_registration,
                    admission_id=self._admission_id
                        if control.control_revision != scope['control_revision'] else '', **reader_args)
        proposed = prepare_unfilled_management_preflight(
            scope=scope, policy=policy, record=record, broker_snapshot=broker_snapshot,
            target=target, observation=observation, assessment=assessment,
            previous=self._unfilled_preflight,
        )
        child_id = proposed["reserved_child_intent_id"]
        if child_id is not None and execution.load(child_id) is not None:
            raise ValueError("unfilled_preflight_native_child_requires_reconciliation")
        self._unfilled_preflight = proposed
        self._save_checkpoint()
        if self._closing:
            raise ValueError("unfilled_preflight_native_state_changed")
        with read_scope() as reader:
            reader_args = {} if reader is None else {'_reader': reader}
            if (execution.load(record.intent.intent_id, **reader_args) != record
                    or self._repository.load_mock_automation_control(self._bundle.account_ref, **reader_args) != control
                    or (child_id is not None and execution.load(child_id, **reader_args) is not None)):
                raise ValueError("unfilled_preflight_native_state_changed")
        return deepcopy(proposed)

    def _register_native_unfilled(self, record, gate, decision=None):
        if self._spec.unfilled_execution_policy is None or record is None:
            return
        if (gate.replacement_binding is not None or gate.action not in {'ENTER', 'EXIT'}
                or gate.spec_id != self._spec.spec_id or gate.execution_run_id != self._bundle.run_id
                or gate.account_ref != self._bundle.account_ref or gate.symbol != record.intent.symbol
                or gate.quantity != record.intent.quantity or gate.limit_price != record.intent.limit_price
                or gate.decided_at != record.intent.created_at
                or gate.intent_id != record.intent.intent_id
                or gate.strategy_decision_id != record.intent.decision_id
                or gate.status.value != 'APPROVED_FOR_SINGLE_SUBMISSION'
                or record.intent.intent_id.startswith('unfilled-native-child:')):
            raise ValueError('unfilled_registration_original_approval_mismatch')
        if self._unfilled_registration and self._unfilled_registration['root_intent']['intent_id'] == record.intent.intent_id:
            return
        if decision is None and gate.action == 'EXIT':
            self._unfilled_source_status = {'state': 'BLOCKED', 'reason': 'recovered_exit_purpose_unavailable'}
            return
        if self._unfilled_preflight is not None:
            # A completed episode alone may release its anchor. Never discard a
            # pending child/UNKNOWN/aggregate fill gap to enroll a new root.
            root_id = self._unfilled_preflight['anchor']['root_intent']['intent_id']
            old = self._bundle.runtime.load_intent(root_id)
            children = self._unfilled_attempt_plan['children'] if self._unfilled_attempt_plan else []
            rows = [self._bundle.runtime.load_intent(row['intent']['intent_id']) for row in children]
            if old is None or any(row is None or row.state not in TERMINAL_ORDER_STATES
                                 or row.filled_quantity != row.detailed_filled_quantity for row in [old, *rows]):
                raise ValueError('unfilled_registration_prior_episode_unresolved')
            self._unfilled_preflight = self._unfilled_attempt_plan = None
        from kiwoom_monitor.application.order_lifecycle import UnfilledOrderManagementPolicy
        from kiwoom_monitor.application.position_target import _json_value
        from kiwoom_monitor.application.mock_automation_session_clock import content_id
        intent, frozen = record.intent, self._spec.unfilled_execution_policy
        purpose = ('BUY_OPPORTUNITY' if gate.action == 'ENTER' else
                   'TAKE_PROFIT' if tuple(decision.reasons) == ('target_reached',) else 'PROTECTIVE_EXIT')
        from kiwoom_monitor.application.order_lifecycle import anchored_unfilled_policy
        policy = anchored_unfilled_policy(intent, frozen, purpose)
        body = {'version': 'mock-unfilled-registration/v1', 'spec_id': self._spec.spec_id,
                'account_ref': self._bundle.account_ref, 'execution_run_id': self._bundle.run_id,
                'control_revision': gate.control_revision, 'operating_policy_id': frozen.policy_id,
                'root_intent': asdict(intent), 'purpose': purpose,
                'target_quantity': intent.quantity if gate.action == 'ENTER' else 0,
                'policy': asdict(policy)}
        body = json.loads(json.dumps(body, default=_json_value))
        registration = {**body, 'registration_id': content_id('unfilled_registration', body)}
        self._validate_unfilled_registration(registration)
        with self._unfilled_input_lock:
            self._unfilled_registration = registration
            self._cancel_recovery = None
            self._unfilled_population = None
            self._unfilled_evidence_cache = None
            self._unfilled_judgment = decision.to_dict() if purpose=='TAKE_PROFIT' and decision is not None else None
            self._unfilled_ordinary_ready = None
            self._unfilled_alignment_ref = None
            self._unfilled_alignment_price = None
            self._unfilled_source_pending = True
            self._unfilled_source_deadline = None
            self._unfilled_source_recovery_deadline = None
            self._unfilled_source_price_ready = None
        self._save_checkpoint()
        request = getattr(self._bundle.monitor, 'request_automation_execution_recovery', None)
        if callable(request):
            request()

    def _validate_unfilled_registration(self, body):
        return unfilled_registration_policy(body, self._spec,
            account_ref=self._bundle.account_ref, run_id=self._bundle.run_id)

    def _unfilled_registration_matches_control(self, control, policy, registration):
        scope = {key: registration[key] for key in ('account_ref', 'execution_run_id', 'spec_id', 'control_revision')}
        scope['candidate_package_hash'] = self._spec.candidate_package_hash
        execution = self._bundle.repository
        read_scope = (self._repository.mock_automation_read_scope
                      if isinstance(execution, ExecutionRepository) and execution.uses_store(self._store)
                      else nullcontext)
        with read_scope() as reader:
            reader_args = {} if reader is None else {'_reader': reader}
            load_intent = self._bundle.runtime.load_intent if reader is None else execution.load
            root = load_intent(policy.anchor_intent_id, **reader_args)
            try:
                unfilled_management_control_revision(self._repository, self._spec, control, scope, policy, root,
                    registration, admission_id=self._admission_id,
                    **reader_args)
            except ValueError:
                return False
        return True

    def _leader_plan_for_decision(self, decision):
        pin = self._leader_position_proof
        if pin is None:
            raise ValueError("leader plan requires the pinned entry evidence")
        plan = OrderPlanRef(pin["entry_intent_id"], pin["proof_id"], 0)
        if decision.final_action == "ENTER":
            return plan
        head = self._bundle.repository.load_plan_position(self._bundle.account_ref, self._bundle.run_id, decision.symbol, plan)
        if (head.unreconciled_sell_quantity
                or decision.final_action == 'EXIT' and head.quantity != decision.quantity
                or decision.final_action == 'REDUCE' and not 0 < decision.quantity < head.quantity
                or decision.final_action == 'ADD' and head.quantity <= 0):
            raise ValueError("leader exit does not match the durable plan position")
        return head.plan

    def _dispatch_original_and_register(self, decision, recovery, risk, *, protection_exit_id=None, plan=None, position_action_ref=None):
        if self._leader_plan_mode and plan is None:
            raise ValueError("native leader plan cannot dispatch a planless order")
        result = dispatch_mock_automation_decision_from_risk(self._repository, self._bundle.runtime, decision,
            recovery, risk, account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id,
            strategy_ref=self._spec.strategy_ref,
            **({"plan": plan} if plan is not None else {}),
            **({'position_action_ref':position_action_ref} if position_action_ref is not None else {}),
            **({'protection_exit_id':protection_exit_id} if protection_exit_id is not None else {}))
        if result[1] is not None and plan is None:
            self._register_native_unfilled(result[1], result[0], decision)
        return result

    async def _produce_native_unfilled_inputs(self, control):
        if self._spec.unfilled_execution_policy is None:
            return
        monitor = getattr(self._bundle, 'monitor', None)
        if not getattr(monitor, 'automation_execution_inputs_enabled', False):
            return
        with self._unfilled_input_lock:
            if self._unfilled_input is not None:
                return  # Explicit supplied cuts keep their existing isolated seam.
        if self._unfilled_registration is None and self._unfilled_preflight is not None:
            return
        if self._unfilled_registration is None and self._unfilled_discovery_pending:
            self._unfilled_discovery_pending = False
            rows = await drain_thread_call(self._bundle.runtime.load_active_intents)
            rows = self._own_active_records(rows)
            if len(rows) == 1:
                gate = await drain_thread_call(self._repository.load_mock_automation_approved_gate,
                                              self._bundle.account_ref, rows[0].intent.intent_id)
                if gate is not None:
                    await drain_thread_call(self._register_native_unfilled, rows[0], gate)
            elif rows:
                self._unfilled_source_status = {'state':'BLOCKED','reason':'unfilled_registration_multiple_active_roots'}
        registration = self._unfilled_registration
        if registration is None:
            return
        policy = self._validate_unfilled_registration(registration)
        now = self._bundle.runtime.server_now()
        price = self._price_source(registration['root_intent']['symbol']) if self._price_source is not None else None
        if self._unfilled_alignment_price is not None:
            price=self._unfilled_alignment_price
        available = bool(price and price.get('durable') is False and price.get('market_status') == 'TRADABLE'
                         and 0 <= (now-price['available_at']).total_seconds() <= policy.max_snapshot_age_seconds)
        if policy.purpose == 'PROTECTIVE_EXIT':
            ready = available and (policy.anchor_price_won-price['price_won'])*10000 <= policy.protective_slippage_bps*policy.anchor_price_won
            if ready != self._unfilled_source_price_ready:
                self._unfilled_source_pending = True
            self._unfilled_source_price_ready = ready
        else:
            population = self._unfilled_population
            ready = (self._current_unfilled_cost(now) is not None,
                bool(population and available and price['price_won']==population['candidate_price']
                     and 0 <= (now-population['available_at']).total_seconds()<=policy.max_snapshot_age_seconds),
                self._current_unfilled_judgment(policy,now)[0])
            if ready != self._unfilled_ordinary_ready:
                self._unfilled_source_pending = True
            self._unfilled_ordinary_ready = ready
        deadline = min(policy.wait_until, policy.protection_deadline if policy.purpose != 'BUY_OPPORTUNITY' else policy.valid_until)
        plan = self._unfilled_attempt_plan or self._unfilled_preflight
        if plan and plan.get('next_check_at'):
            deadline = datetime.fromisoformat(plan['next_check_at'])
        if self._cancel_recovery is not None:
            next_check = self._cancel_recovery.get('next_check_at')
            deadline = datetime.fromisoformat(next_check) if next_check else min(policy.valid_until,policy.protection_deadline)
            if now >= min(policy.valid_until,policy.protection_deadline) and self._cancel_recovery['state'] not in {'RESOLVED','ESCALATE'}:
                self._cancel_recovery.update(state='ESCALATE',reason='original_protection_deadline_reached',next_check_at=None)
                self._status.update(requires_operator_attention=True,cancel_confirmation_required=True)
                await drain_thread_call(self._save_checkpoint)
        due = now >= deadline and self._unfilled_source_deadline != deadline
        if not self._unfilled_source_pending and not due:
            return
        cut = getattr(monitor, 'latest_automation_execution_cut', None)
        if due and self._unfilled_source_recovery_deadline != deadline:
            self._unfilled_source_recovery_deadline = deadline
            monitor.request_automation_execution_recovery()
        superseded_revision = (self._unfilled_source_status or {}).get('superseded_risk_revision')
        if superseded_revision is not None and (
                cut is None or cut['risk'].reconciliation_revision <= superseded_revision):
            return  # The monitor must acknowledge a newer cut before another assessment.
        if cut is None:
            self._unfilled_source_status = {'state':'BLOCKED','reason':'native_account_cut_unavailable'}
            return  # Wait for owner's ACK callback, not a repeated broker request.
        if (registration['control_revision'] != control.control_revision
                and not await drain_thread_call(self._unfilled_registration_matches_control,
                                                control, policy, registration)):
            self._unfilled_source_status = {'state':'BLOCKED','reason':'registration_control_revision_changed'}
            return
        risk, recovery, observation = cut['risk'], cut['recovery'], cut['observation']
        generation = self._unfilled_source_generation
        population = self._unfilled_population
        if (not observation.open_orders_complete or not observation.reconciliation_complete
                or observation.unmapped_broker_order_ids
                or any(not 0 <= (now-at).total_seconds() <= policy.max_snapshot_age_seconds
                       for at in (risk.observed_at, observation.observed_at, observation.account.as_of))):
            self._unfilled_source_status = {'state':'BLOCKED','reason':'native_account_cut_incomplete_or_expired'}
            return
        intent_id = (self._unfilled_attempt_plan['children'][-1]['intent']['intent_id']
                     if self._unfilled_attempt_plan and self._unfilled_attempt_plan['children']
                     else registration['root_intent']['intent_id'])
        record = await drain_thread_call(self._bundle.runtime.load_intent, intent_id)
        if record is None and intent_id != policy.anchor_intent_id:
            record = await drain_thread_call(self._bundle.runtime.load_intent,
                self._unfilled_attempt_plan['children'][-1]['parent_intent_id'])
        broker = next((value for value in recovery.orders if record and value.broker_order_id == record.broker_order_id), None)
        if broker is None:
            self._unfilled_source_status = {'state':'BLOCKED','reason':'managed_broker_confirmation_unavailable'}
            return
        previous=self._unfilled_input_identity
        source=population
        protecting = policy.purpose == 'PROTECTIVE_EXIT' or self._protection_pending()
        decision_cut = now if protecting and record.state in {OrderState.ACCEPTED,OrderState.PARTIALLY_FILLED} else risk.observed_at
        stamps=[price['available_at']] if available else []
        if source is not None and not self._protection_pending() and policy.purpose!='PROTECTIVE_EXIT':
            stamps.append(source['available_at'])
        judgment,judgment_at=self._current_unfilled_judgment(policy,now)
        if judgment_at is not None:stamps.append(judgment_at)
        if (decision_cut==risk.observed_at and any(stamp>risk.observed_at for stamp in stamps)
                or previous is not None and (decision_cut,risk.reconciliation_revision)==previous[:2]):
            ref=(registration['registration_id'],source['input_ref'] if source else None,
                 (self._unfilled_judgment or {}).get('decision_id'),deadline,self._unfilled_ordinary_ready)
            if self._unfilled_alignment_ref!=ref:
                self._unfilled_alignment_ref=ref
                self._unfilled_alignment_price=deepcopy(price) if available else None
                if not due:monitor.request_automation_execution_recovery()
            self._unfilled_source_status={'state':'BLOCKED','reason':'awaiting_aligned_native_decision_cut'}
            return  # Retain original inputs and wait for real owner ACK, not invented time.
        from kiwoom_monitor.application.position_target import PositionTargetShadowRequest
        from kiwoom_monitor.application.order_lifecycle import UnfilledOrderAssessment
        protecting = policy.purpose == 'PROTECTIVE_EXIT' or self._protection_pending()
        cost = None if protecting else self._current_unfilled_cost(now)
        target = PositionTargetShadowRequest(self._bundle.account_ref,'mock','KRX',record.intent.symbol,
            ('ADD' if dict(risk.owned_position_quantities).get(record.intent.symbol, 0) else 'REENTER')
                if record.intent.side is OrderSide.BUY else 'EXIT',
            registration['target_quantity'], price['price_won'] if available else policy.anchor_price_won,
            price['available_at'] if available else policy.anchor_available_at, decision_cut,
            'native-unfilled-target/v1', registration['registration_id'], observation.input_ref, observation.input_ref,
            policy.max_snapshot_age_seconds, observation.reconciliation_complete, observation.open_orders_complete, cost)
        thesis_until = min(policy.wait_until, policy.anchor_available_at + timedelta(seconds=policy.max_snapshot_age_seconds))
        flat_unknown_pnl = (risk.daily_net_pnl_won is None
                            and self._flat_unfilled_buy_cancel_cut(policy, observation, risk))
        assessment = UnfilledOrderAssessment(True if record.intent.side is OrderSide.BUY else None, None,
            not protecting and not flat_unknown_pnl, protecting, 'original-decision:' + registration['root_intent']['decision_id'],
            policy.anchor_available_at, thesis_until, 'native-risk:' + risk.snapshot_id, risk.observed_at,
            risk.observed_at + timedelta(seconds=policy.max_snapshot_age_seconds),
            'TRADABLE' if available else 'UNKNOWN', price['price_won'] if available else None,
            price['input_ref'] if available else '', price['available_at'] if available else None, 0, None)
        paired = {'ev_status':'EV_UNAVAILABLE','reason':'unfilled_paired_population_not_selected'}
        if not protecting:
            stamp = judgment_at
            if stamp is not None:
                assessment = replace(assessment,thesis_valid=judgment is not None,trend_allows_wait=judgment,
                    thesis_input_ref=self._unfilled_judgment['decision_id'],thesis_available_at=stamp,
                    thesis_valid_until=min(policy.wait_until,stamp+timedelta(seconds=policy.max_snapshot_age_seconds)))
            quantity=broker.remaining_quantity
            if record.state is OrderState.CANCELLED:
                held=observation.account.positions.get(record.intent.symbol,0)
                quantity=max(0,registration['target_quantity']-held) if record.intent.side is OrderSide.BUY else max(0,held-registration['target_quantity'])
            if population and available and quantity>0 and record.state in {OrderState.ACCEPTED,OrderState.PARTIALLY_FILLED,OrderState.CANCELLED}:
                evidence, paired = await drain_thread_call(self._prepare_native_unfilled_evidence,population,
                    policy,record,quantity,observation.account.positions.get(record.intent.symbol,0),
                    registration['target_quantity'],price['price_won'],cost,now)
                assessment = replace(assessment,reprice_evidence=evidence)
        # Existing recovery writer links the ACKed cut; no synthetic EV or new
        # collection. The legacy gate still revalidates the current native Risk.
        await drain_thread_call(self._record_recovery, recovery, risk)
        if generation != self._unfilled_source_generation:
            self._unfilled_source_pending = True
            self._unfilled_source_status = {'state':'BLOCKED','reason':'native_account_cut_superseded'}
            return  # A newer ACK callback must not be erased by this older cut.
        self.offer_unfilled_execution_inputs(_native=True, policy=policy, broker_snapshot=broker, target=target,
            observation=observation, assessment=assessment, recovery=recovery, risk=risk)
        self._unfilled_source_pending = False
        self._unfilled_alignment_price=None
        self._unfilled_alignment_ref=None
        self._unfilled_source_facts = self._native_unfilled_facts(risk)
        if due:
            self._unfilled_source_deadline = deadline
        self._unfilled_source_status = {'state':'DELIVERED','risk_snapshot_id':risk.snapshot_id,
            'price_input_ref': price['input_ref'] if available else None,
            'paired_ev':('EMPIRICAL_ESTIMATE' if paired.get('comparison',{}).get('ev_status')=='EMPIRICAL_ESTIMATE' else 'UNAVAILABLE'),
            'paired_input_ref':paired.get('input_ref'),'cost_model_available':cost is not None,
            'paired_unavailable_reason':paired.get('reason'),
            'judgment_input_ref':assessment.thesis_input_ref,
            'registration_id':registration['registration_id']}

    def _current_unfilled_cost(self, now):
        model = self._unfilled_cost_model
        if (model is not None and model.source and model.rate_basis!='unspecified' and model.valid_from
                and datetime.fromisoformat(model.valid_from)<=now<datetime.fromisoformat(model.valid_to)):
            return model
        return None

    def _current_unfilled_judgment(self, policy, now):
        row = self._unfilled_judgment
        if policy.purpose!='TAKE_PROFIT' or row is None:
            return None,None
        stamp=datetime.fromisoformat(row['decided_at'])
        if not 0 <= (now-stamp).total_seconds()<=policy.max_snapshot_age_seconds or now>=policy.wait_until:
            return None,None
        return row['final_action']=='HOLD' if row['final_action'] in {'HOLD','EXIT'} else None,stamp

    def offer_unfilled_reprice_outcomes(self, *, root_intent_id, managed_intent_id,
                                      population_ref, available_at, outcomes, source_publication=None,
                                      expected_registration_id=None):
        """One explicit bounded RAM population, not persisted or replayed as approval."""
        from kiwoom_monitor.application.research_evaluation import UnfilledActionOutcome
        from kiwoom_monitor.application.position_target import _json_value
        frozen=self._spec.unfilled_execution_policy
        registration=self._unfilled_registration
        if (frozen is None or not frozen.allow_supplied_paired_mock_evidence or registration is None
                or registration['purpose']=='PROTECTIVE_EXIT' or root_intent_id!=registration['root_intent']['intent_id']
                or not isinstance(managed_intent_id,str) or not managed_intent_id
                or not isinstance(population_ref,str) or not population_ref.strip()
                or not isinstance(available_at,datetime) or available_at.tzinfo is None
                or not 0 <= (self._bundle.runtime.server_now()-available_at).total_seconds()<=frozen.max_snapshot_age_seconds
                or not isinstance(outcomes,tuple) or not outcomes or len(outcomes)>10_000
                or any(not isinstance(row,UnfilledActionOutcome) for row in outcomes)
                or len({row.input_ref for row in outcomes})!=len(outcomes)
                or len({(row.action,row.case_ref) for row in outcomes})!=len(outcomes)
                or any(row.symbol!=registration['root_intent']['symbol'] or row.purpose!=registration['purpose'] for row in outcomes)
                or len({row.candidate_limit_price_won for row in outcomes})!=1):
            raise ValueError('unfilled_population_not_admitted_or_out_of_scope')
        ids=(root_intent_id,*(row['intent']['intent_id'] for row in (self._unfilled_attempt_plan or {}).get('children',())))
        if managed_intent_id not in ids:
            raise ValueError('unfilled_population_managed_order_out_of_scope')
        body={'root_intent_id':root_intent_id,'managed_intent_id':managed_intent_id,'population_ref':population_ref,
              'available_at':available_at,'outcomes':outcomes,'candidate_price':outcomes[0].candidate_limit_price_won}
        if source_publication is not None:body['source_publication']=deepcopy(source_publication)
        encoded=json.dumps({**body,'outcomes':[asdict(row) for row in outcomes]},sort_keys=True,separators=(',',':'),default=_json_value).encode()
        if len(encoded)>1024*1024:
            raise ValueError('unfilled_population_budget_exceeded')
        ref='unfilled-population:'+hashlib.sha256(encoded).hexdigest()
        with self._unfilled_input_lock:
            if self._closing:raise RuntimeError('MOCK_AUTOMATION_RUNNER_CLOSED')
            if (self._unfilled_registration is not registration or expected_registration_id is not None
                    and expected_registration_id!=registration['registration_id']):
                raise ValueError('unfilled_population_registration_changed')
            old=self._unfilled_population
            if old and available_at<old['available_at']:raise ValueError('unfilled_population_out_of_order')
            if old and ref==old['input_ref']:return False
            self._unfilled_population={'input_ref':ref,**body}
            self._unfilled_evidence_cache=None
            self._unfilled_alignment_ref=None;self._unfilled_alignment_price=None
            self._unfilled_source_generation+=1;self._unfilled_source_pending=True
        notify = self._wake_notification()
        if notify is not None:
            notify()
        return True

    def offer_native_unfilled_publication(self, document):
        """One reported PC native packet; current execution gates remain authoritative."""
        from kiwoom_monitor.application.research_evaluation import (
            unfilled_population_publication_from_dict,position_action_cost_model_ref)
        outcomes,published=unfilled_population_publication_from_dict(document)
        frozen=self._spec.unfilled_execution_policy
        registration=self._unfilled_registration
        if (registration is None or frozen is None or not frozen.allow_supplied_paired_mock_evidence
                or (document['account_ref'],document['credential_profile_id'],document['spec_id'],document['execution_run_id'],
                    document['expected_control_revision'])!=(self._bundle.account_ref,self._spec.credential_profile_id,
                    self._spec.spec_id,self._bundle.run_id,self._bundle.automation_context.control_revision)
                or registration['control_revision']!=document['expected_control_revision']
                or self._status.get('state') in {'STOPPED','ERROR'}):
            raise ValueError('native_unfilled_publication_active_scope_mismatch')
        if (registration['purpose']=='PROTECTIVE_EXIT' or outcomes[0].horizon_seconds!=frozen.reprice_horizon_seconds
                or len(outcomes)//2<frozen.minimum_paired_samples
                or outcomes[0].target_quantity!=registration['target_quantity']
                or outcomes[0].cost_model_ref!=position_action_cost_model_ref(
                    self._unfilled_cost_model.to_dict() if self._unfilled_cost_model else None)):
            raise ValueError('native_unfilled_publication_frozen_model_mismatch')
        summary={'publication_ref':document['publication_ref'],'version':document['version'],
            'source_receipt':deepcopy(document['source_receipt']),
            'native_validation':'PC_READONLY_REPLAY_REPORTED','source_durability':'not_verified','order_authorized':False}
        changed=self.offer_unfilled_reprice_outcomes(root_intent_id=document['root_intent_id'],
            managed_intent_id=document['managed_intent_id'],population_ref=outcomes[0].population_ref,
            available_at=published,outcomes=outcomes,source_publication=summary,
            expected_registration_id=registration['registration_id'])
        return {'status':'accepted' if changed else 'unchanged','publication_ref':document['publication_ref'],
            'available_at':published.isoformat(),'source_durability':'not_verified',
            'native_validation':'PC_READONLY_REPLAY_REPORTED','order_authorized':False}

    def _prepare_native_unfilled_evidence(self, population, policy, record, remaining, held, target, price, cost, now):
        if population['managed_intent_id']!=record.intent.intent_id:
            return None,{'ev_status':'EV_UNAVAILABLE','reason':'population_managed_order_changed'}
        if population.get('source_publication') and record.state is not OrderState.ACCEPTED:
            return None,{'ev_status':'EV_UNAVAILABLE','reason':'native_unfilled_model_state_unsupported'}
        key=(population['input_ref'],record.intent.intent_id,record.state.value,remaining,held,target,price,cost,
             0 <= (now-population['available_at']).total_seconds()<=policy.max_snapshot_age_seconds)
        if self._unfilled_evidence_cache and self._unfilled_evidence_cache[0]==key:
            return self._unfilled_evidence_cache[1]
        value=prepare_unfilled_reprice_evidence(population['outcomes'],policy=policy,record=record,
            pending_quantity=remaining,held_quantity=held,target_quantity=target,candidate_price=price,cost_model=cost,
            population_ref=population['population_ref'],available_at=population['available_at'],decision_time=now)
        self._unfilled_evidence_cache=(key,value)
        return value

    def _native_unfilled_facts(self, risk):
        response_at = (self._cancel_recovery or {}).get('reject_occurred_at')
        post_rejection = bool(response_at and risk.account_as_of > datetime.fromisoformat(response_at))
        return (getattr(self._bundle.monitor, 'automation_execution_revision', None),
                risk.owned_position_quantities, risk.pending_sell_quantities, risk.reconciliation_complete,
                risk.submission_unknown_count, risk.daily_net_pnl_won, risk.cost_complete,
                bool(risk.balance_mismatch_count), risk.reconnect_count, post_rejection)

    @staticmethod
    def _flat_unfilled_buy_cancel_cut(policy, observation, risk):
        # A known zero-FILL reservation can have complete position evidence
        # while account PnL is unavailable. This cut permits cancellation only;
        # it never upgrades Risk or admits a replacement/new BUY.
        return (policy.purpose == 'BUY_OPPORTUNITY'
                and (not risk.reconciliation_complete or risk.daily_net_pnl_won is None)
                and observation.open_orders_complete and observation.reconciliation_complete
                and not observation.unmapped_broker_order_ids
                and not risk.submission_unknown_count and not risk.balance_mismatch_count
                and not risk.owned_position_quantities and not risk.pending_sell_quantities
                and not any(observation.account.positions.values())
                and bool(observation.open_orders)
                and all(row.intent.side is OrderSide.BUY and row.state is OrderState.ACCEPTED
                        and row.filled_quantity == row.detailed_filled_quantity == row.broker_reported_filled_quantity == 0
                        for row in observation.open_orders))

    def offer_unfilled_execution_inputs(self, *, _native=False, **inputs) -> bool:
        """Replace one RAM cut and wake the existing owner; never grant authority.

        The producer must supply current typed evidence. No broker refresh,
        calculation, checkpoint or order is performed by this notification.
        Cuts are not restored/replayed on restart; native plans remain durable.
        """
        from kiwoom_monitor.application.order_lifecycle import UnfilledOrderManagementPolicy, UnfilledOrderAssessment
        from kiwoom_monitor.application.position_target import PositionAccountObservation, PositionTargetShadowRequest, _json_value
        from kiwoom_monitor.application.mock_automation_risk import MockAutomationRiskSnapshot
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
        from kiwoom_monitor.domain.order_contract import BrokerOrderSnapshot
        types = {'policy': UnfilledOrderManagementPolicy, 'assessment': UnfilledOrderAssessment,
                 'observation': PositionAccountObservation, 'target': PositionTargetShadowRequest,
                 'risk': MockAutomationRiskSnapshot, 'recovery': MockAccountRecovery,
                 'broker_snapshot': BrokerOrderSnapshot}
        if set(inputs) != set(types) or any(not isinstance(inputs[key], kind) for key, kind in types.items()):
            raise ValueError('unfilled_current_input_types_or_confirmation_missing')
        frozen = self._spec.unfilled_execution_policy
        if frozen is None:
            raise ValueError('unfilled_execution_policy_off')
        snapshot = deepcopy(inputs)
        target, risk, observation, recovery = (snapshot[key] for key in ('target', 'risk', 'observation', 'recovery'))
        now = self._bundle.runtime.server_now()
        if (target.account_ref != self._bundle.account_ref or risk.account_ref != self._bundle.account_ref
                or risk.execution_run_id != self._bundle.run_id or observation.account != recovery.account
                or observation.risk_snapshot != risk
                or any(not 0 <= (now - cut).total_seconds() <= frozen.max_snapshot_age_seconds
                       for cut in (target.decision_time, risk.observed_at, observation.observed_at))):
            raise ValueError('unfilled_current_input_scope_or_time_mismatch')
        encoded_values = {key: asdict(value) for key, value in snapshot.items() if key != 'risk'}
        encoded_values['risk'] = risk.to_dict()
        # The observation carries the same typed Risk, including trading_date.
        encoded_values['observation']['risk_snapshot'] = encoded_values['risk']
        encoded = json.dumps(encoded_values,
                             sort_keys=True, separators=(',', ':'), default=_json_value).encode()
        if len(encoded) > 1024 * 1024:
            raise ValueError('unfilled_current_input_budget_exceeded')
        identity = (target.decision_time, risk.reconciliation_revision, hashlib.sha256(encoded).hexdigest())
        broker, assessment = snapshot['broker_snapshot'], snapshot['assessment']
        evidence = assessment.reprice_evidence
        supported_ev = bool(evidence and evidence.net_advantage_won - snapshot['policy'].uncertainty_multiplier *
                            evidence.net_standard_error_won > snapshot['policy'].minimum_net_advantage_won)
        # Availability timestamps, quote changes and healthy REST revisions are
        # freshness, not new order/risk/thesis events. They may renew the one cut
        # held for a future deadline without repeating the expensive gate.
        cause = (broker.broker_order_id, broker.state.value, broker.filled_quantity, broker.remaining_quantity,
                 tuple(sorted((fill.execution_id, fill.quantity, fill.price) for fill in broker.fills)),
                 risk.owned_position_quantities, risk.pending_sell_quantities, risk.reconciliation_complete,
                 risk.submission_unknown_count, risk.daily_net_pnl_won, risk.cost_complete,
                 risk.balance_mismatch_count > 0, risk.reconnect_count,
                 assessment.thesis_valid, assessment.trend_allows_wait, assessment.risk_allows_new_risk,
                 assessment.protection_required, assessment.market_status, supported_ev)
        with self._unfilled_input_lock:
            if self._closing:
                raise RuntimeError('MOCK_AUTOMATION_RUNNER_CLOSED')
            previous = self._unfilled_input_identity
            if previous is not None:
                if identity == previous:
                    return False
                if (identity[0] < previous[0] or identity[1] < previous[1]
                        or identity[:2] == previous[:2]):
                    raise ValueError('unfilled_current_input_out_of_order_or_conflicting_cut')
            self._unfilled_input_identity = identity
            self._unfilled_input = (identity[2], snapshot, cause, _native)
            self._unfilled_delivery = {'input_id': identity[2], 'decision_time': identity[0].isoformat(),
                                      'risk_revision': identity[1], 'state': 'PENDING', 'order_authorized': False}
            should_wake = cause != self._unfilled_processed_cause or self._unfilled_due() is not None
            notify = self._wake_notification(tolerate_retired_loop=True)
        if should_wake and notify is not None:
            notify()
        return True

    def _unfilled_due(self):
        plan = self._unfilled_attempt_plan or self._unfilled_preflight
        due = plan.get('next_check_at') if plan else None
        if self._cancel_recovery is not None:
            due = self._cancel_recovery.get('next_check_at')
        if due and due != self._unfilled_processed_deadline and self._bundle.runtime.server_now() >= datetime.fromisoformat(due):
            return due
        return None

    async def _unfilled_call(self) -> bool:
        if self._spec.unfilled_execution_policy is None:
            return False
        with self._unfilled_input_lock:
            pending = self._unfilled_input is not None
            if pending and any((self._bundle.runtime.server_now() - cut).total_seconds() > self._spec.unfilled_execution_policy.max_snapshot_age_seconds
                               for cut in (self._unfilled_input[1]['target'].decision_time, self._unfilled_input[1]['risk'].observed_at)):
                self._unfilled_input = None
                self._unfilled_delivery.update(state='BLOCKED', reason='unfilled_cached_input_expired')
                pending = False
            eligible = pending and (self._unfilled_input[2] != self._unfilled_processed_cause
                                    or self._unfilled_due() is not None)
            if pending and not eligible:
                return False
        if not pending:
            # A due clock cannot create evidence or repeatedly run the full gate.
            plan = self._unfilled_attempt_plan or self._unfilled_preflight
            if plan and plan.get('next_check_at') and self._bundle.runtime.server_now() >= datetime.fromisoformat(plan['next_check_at']):
                with self._unfilled_input_lock:
                    if self._unfilled_delivery is None or self._unfilled_delivery.get('state') != 'CURRENT_INPUT_REQUIRED':
                        self._unfilled_delivery = {'state': 'CURRENT_INPUT_REQUIRED', 'next_check_at': plan['next_check_at'],
                                                  'order_authorized': False}
                if (plan['result']['risk_path_required'] and not plan['result']['liquidation_confirmed']
                        and not self._protection_pending()):
                    self.request_protection_review(reason='unfilled_protection_deadline_current_input_required',
                        input_ref='unfilled-deadline:' + plan['receipt_id'], requested_at=self._bundle.runtime.server_now())
            return False  # Preserve the independent risk/session clock path.
        # Close joins the whole operation and its receipt/checkpoint, including
        # cancellation arriving while a native thread is claiming/submitting.
        task = asyncio.create_task(self._poll_unfilled_execution())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled() and task.exception() is not None:
                logger.error('unfilled execution failed while draining', exc_info=task.exception())
            raise

    async def _poll_unfilled_execution(self) -> bool:
        if self._checkpoint.pending:
            await drain_thread_call(self._save_checkpoint)
        with self._unfilled_input_lock:
            pending, self._unfilled_input = self._unfilled_input, None
        if pending is None:
            return False
        identity, inputs, cause, native = pending
        state, reason = 'PROCESSED', None
        try:
            now = self._bundle.runtime.server_now()
            age = self._spec.unfilled_execution_policy.max_snapshot_age_seconds
            if self._closing or any(not 0 <= (now - cut).total_seconds() <= age
                                   for cut in (inputs['target'].decision_time, inputs['risk'].observed_at)):
                raise ValueError('unfilled_current_input_expired_before_processing')
            protective = (inputs['policy'].purpose == 'PROTECTIVE_EXIT' or
                          inputs['assessment'].protection_required and inputs['target'].action == 'EXIT')
            if self._protection_pending() and not protective:
                if inputs['policy'].purpose != 'BUY_OPPORTUNITY':
                    raise ValueError('protection_review_blocks_opportunity_input')
                # Existing exposure may be cancelled while new BUY risk stays shut.
                inputs = {**inputs, 'assessment': replace(inputs['assessment'], protection_required=True)}
            self._unfilled_processed_cause = cause
            self._unfilled_processed_deadline = self._unfilled_due()
            if self._unfilled_preflight is None:
                await drain_thread_call(self._preview_unfilled_management_locked,
                    **{key: value for key, value in inputs.items() if key not in {'recovery', 'risk'}})
            result = await self._manage_unfilled_inputs(inputs, native=native)
            state, reason = result
        except ValueError as error:
            state, reason = 'BLOCKED', str(error)
        except Exception as error:
            state, reason = 'ERROR', str(error)
            raise
        finally:
            with self._unfilled_input_lock:
                # A newer producer cut may arrive while the native ACK is pending.
                if self._unfilled_delivery is not None and self._unfilled_delivery.get('input_id') == identity:
                    self._unfilled_delivery.update(state=state, reason=reason)
        return True

    def _assess_unfilled_action_locked(self, inputs):
        plan = self._unfilled_attempt_plan
        if plan and plan['children']:
            last = self._bundle.runtime.load_intent(plan['children'][-1]['intent']['intent_id'])
            if last is not None and last.broker_order_id != inputs['broker_snapshot'].broker_order_id:
                # Historical confirmation cannot authorize management of a newer
                # child. The old once-only recovery seam still restores receipts.
                return None
        return self._preview_unfilled_execution_locked(**inputs)

    async def _manage_unfilled_inputs(self, inputs, *, native):
        if self._cancel_recovery is not None and inputs['policy'].purpose == 'PROTECTIVE_EXIT':
            record = await drain_thread_call(self._bundle.runtime.load_intent,self._cancel_recovery['managed_intent_id'])
            events = await drain_thread_call(self._cancel_lineage_events)
            self._set_cancel_recovery(protective_cancel_recovery(record,events,inputs['policy'],
                self._spec.unfilled_execution_policy,self._bundle.runtime.server_now()),events,inputs['policy'])
        try:
            plan = await drain_thread_call(self._assess_unfilled_action_locked, inputs)
        except MockAutomationRiskSuperseded:
            cue = self._cancel_recovery
            if (not native or self._closing or inputs['policy'].purpose != 'PROTECTIVE_EXIT'
                    or not cue or cue['state'] != 'RETRY_READY'
                    or self._unfilled_processed_deadline != cue['next_check_at']):
                raise
            # No native claim has started. Re-arm the same deadline and keep its
            # existing recovery request deduplication and durable retry budget.
            self._unfilled_source_deadline = None
            self._unfilled_processed_deadline = None
            self._unfilled_source_pending = True
            reason = 'unfilled_native_risk_superseded'
            self._unfilled_source_status = {'state': 'BLOCKED', 'reason': reason,
                'superseded_risk_revision': inputs['risk'].reconciliation_revision}
            notify = self._wake_notification()
            if notify is not None:
                notify()
            return 'BLOCKED', reason
        if plan is not None:
            action = plan['result']['action']
            if action != 'REPRICE':
                if action == 'CANCEL':
                    return await self._cancel_unfilled(inputs, plan)
                if plan['result']['status'] == 'BLOCKED':
                    return 'BLOCKED', ';'.join(plan['result']['reasons'])
                return ('ABANDONED' if action == 'ABANDON' else 'WAITING'), ';'.join(plan['result']['reasons'])
        if plan is None:
            gate, record, _ = await drain_thread_call(self._execute_unfilled_replacement_locked, **inputs)
        else:
            gate, record, _ = await drain_thread_call(self._dispatch_unfilled_replacement_locked, inputs, plan)
        return ('PROCESSED', None) if record is not None else ('BLOCKED', ';'.join(gate.reasons))

    def _read_unfilled_cancel_inputs(self, policy, plan):
        execution = self._bundle.repository
        protective = policy.purpose == 'PROTECTIVE_EXIT'
        read_scope = (execution.mock_automation_read_scope
                      if protective and isinstance(execution, ExecutionRepository) and execution.uses_store(self._store)
                      else nullcontext)
        with read_scope() as reader:
            reader_args = {} if reader is None else {'_reader': reader}
            load_intent = self._bundle.runtime.load_intent if reader is None else execution.load
            root = load_intent(policy.anchor_intent_id, **reader_args)
            reasons = unfilled_execution_policy_rejections(self._spec, policy, root)
            if (plan['result']['after_cancel'] == 'REPRICE' and plan['result']['purpose'] != 'PROTECTIVE_EXIT'
                    and (not self._spec.unfilled_execution_policy.allow_supplied_paired_mock_evidence
                         or plan['result']['ev_status'] != 'SUPPLIED_PAIRED_UNFILLED_ESTIMATE')):
                reasons += ('UNFILLED_PAIRED_EVIDENCE_NOT_ADMITTED_FOR_MOCK',)
            if reasons:
                raise ValueError(';'.join(reasons))
            intent_ref = plan['result']['intent_ref']
            expected = root if intent_ref == policy.anchor_intent_id else load_intent(intent_ref, **reader_args)
            events = self._cancel_lineage_events(**reader_args) if protective else ()
        return expected, events

    async def _cancel_unfilled(self, inputs, plan):
        gateway = getattr(self._bundle, 'gateway', None)
        if gateway is None:
            raise ValueError('unfilled_automation_cancel_gateway_unavailable')
        expected, events = await drain_thread_call(self._read_unfilled_cancel_inputs, inputs['policy'], plan)
        proof = {}
        retry_claim = None
        if inputs['policy'].purpose == 'PROTECTIVE_EXIT':
            if any(row['event_type']=='CANCEL_STARTED' and row['intent_id']==expected.intent.intent_id for row in events):
                cue = protective_cancel_recovery(expected,events,inputs['policy'],self._spec.unfilled_execution_policy,
                                                 self._bundle.runtime.server_now())
                if cue['state'] != 'RETRY_READY':
                    self._set_cancel_recovery(cue,events,inputs['policy'])
                    await drain_thread_call(self._save_checkpoint)
                    return 'CANCEL_UNCONFIRMED',cue['reason']
                retry_claim = {key:cue[key] for key in ('root_intent_id','policy_id','reject_event_id','attempt')}
        def revalidate():
            self._revalidate_unfilled_cancel(inputs,plan,expected,proof)
            if retry_claim is not None:
                retry_claim.update(risk_snapshot_id=proof['cancel_risk_snapshot_id'],
                                   risk_reconciliation_revision=proof['cancel_risk_reconciliation_revision'])
        record = await gateway.cancel_automation_intent(expected, spec_id=self._spec.spec_id,
            control_revision=plan['native_gate']['control_revision'],
            before_cancel_claim=revalidate,
            **({'cancel_retry_claim':retry_claim} if retry_claim is not None else {}))
        unresolved = record.state not in TERMINAL_ORDER_STATES
        rejected = unresolved and record.state is not OrderState.CANCEL_PENDING
        state = 'CANCEL_UNCONFIRMED' if rejected else 'CANCEL_REQUESTED' if unresolved else 'CANCEL_RESOLVED'
        if rejected and plan['result']['risk_path_required'] and not self._protection_pending():
            # The gateway has returned an admitted operation. Keep its recovery cue
            # during drain without waking the closed runner or admitting new work.
            self._record_protection_review(reason='unfilled_protective_cancel_unconfirmed',
                input_ref='native-cancel:' + record.intent.intent_id, requested_at=self._bundle.runtime.server_now(),
                draining_cancel=True)
        self._status = {'state': state, 'reason': 'unfilled_native_cancel',
                        'pending_intent_id': record.intent.intent_id, 'orders_enabled': False,
                        'cancel_confirmation_required': unresolved,
                        'requires_operator_attention': rejected,
                        'after_cancel': plan['result']['after_cancel'], **proof}
        if inputs['policy'].purpose == 'PROTECTIVE_EXIT':
            events = await drain_thread_call(self._cancel_lineage_events)
            self._set_cancel_recovery(protective_cancel_recovery(record,events,inputs['policy'],
                self._spec.unfilled_execution_policy,self._bundle.runtime.server_now()),events,inputs['policy'])
        await drain_thread_call(self._save_checkpoint)
        # Cancellation ACK is not a terminal broker/FILL observation. Refresh
        # through the existing coalesced account owner once per actual command;
        # a flat BUY cancellation also invalidates the previously complete Risk.
        request = getattr(self._bundle.monitor, 'request_automation_execution_recovery', None)
        if callable(request):
            request()
        return state, None

    def _cancel_lineage_events(self, *, _reader=None):
        plan = self._unfilled_attempt_plan
        ids = (plan['anchor']['root_intent']['intent_id'],*(child['intent']['intent_id'] for child in plan['children']))
        reader_args = {} if _reader is None else {'_reader': _reader}
        return tuple(row for intent_id in ids for row in self._bundle.repository.events(intent_id, **reader_args))

    def _set_cancel_recovery(self, cue, events, policy):
        rejected = next((row for row in reversed(events) if row['event_type']=='CANCEL_REJECTED'
                         and row['intent_id']==cue['managed_intent_id']),None)
        if rejected:
            cue['reject_occurred_at'] = rejected['occurred_at']
        if cue['state'] == 'WAIT_RECONCILIATION':
            cue['next_check_at'] = min(policy.valid_until,policy.protection_deadline).isoformat()
        self._cancel_recovery = cue
        self._status.update(requires_operator_attention=cue['state']=='ESCALATE',
                            cancel_confirmation_required=cue['state']!='RESOLVED')

    def _revalidate_unfilled_cancel(self, inputs, plan, expected, proof):
        """Called inside the original runtime lock, after the gateway's REST cut.

        Reuse ACKed owner RAM account/Risk and native broker confirmation. Never
        renew thesis, quote or paired EV timestamps from the earlier assessment.
        """
        now = self._bundle.runtime.server_now()
        age = self._spec.unfilled_execution_policy.max_snapshot_age_seconds
        if self._closing or not 0 <= (now - inputs['target'].decision_time).total_seconds() <= age:
            raise ValueError('unfilled_cancel_input_expired_or_closing_before_claim')
        monitor = self._bundle.monitor
        cut = getattr(monitor, 'latest_automation_risk_cut', None)
        observation = getattr(monitor, 'latest_position_observation', None)
        if (cut is None or observation is None or observation.risk_snapshot != cut['risk']
                or observation.account != cut['account'] or not observation.open_orders_complete
                or not observation.reconciliation_complete or observation.unmapped_broker_order_ids):
            raise ValueError('unfilled_cancel_current_owner_account_risk_unavailable')
        risk = cut['risk']
        flat_buy_cancel = (plan['result']['after_cancel'] == 'ABANDON'
                          and self._flat_unfilled_buy_cancel_cut(inputs['policy'], observation, risk))
        execution = self._bundle.repository
        read_scope = (self._repository.mock_automation_read_scope
                      if isinstance(execution, ExecutionRepository) and execution.uses_store(self._store)
                      else nullcontext)
        with read_scope() as reader:
            reader_args = {} if reader is None else {'_reader': reader}
            if (risk != self._repository.load_latest_mock_automation_risk(self._bundle.account_ref, **reader_args)
                    or risk.account_ref != self._bundle.account_ref or risk.execution_run_id != self._bundle.run_id
                    or (not risk.reconciliation_complete and not flat_buy_cancel) or not self._episode_position.matches_risk(risk)
                    or any(not 0 <= (now - stamp).total_seconds() <= age
                           for stamp in (risk.observed_at, observation.observed_at, observation.account.as_of))):
                raise ValueError('unfilled_cancel_native_risk_or_owned_state_changed')
            trigger = self._native_protection_trigger(risk,
                now=now, account_positions=observation.account.positions)
            if (trigger is not None and trigger['disposition'] == 'RECONCILE_REQUIRED'
                    and inputs['policy'].purpose != 'BUY_OPPORTUNITY'):
                raise ValueError('unfilled_cancel_native_reconciliation_required')
            if (plan['result']['after_cancel'] == 'REPRICE' and plan['result']['purpose'] != 'PROTECTIVE_EXIT'
                    and (not risk.cost_complete or risk.daily_net_pnl_won is None)):
                raise ValueError('unfilled_cancel_current_cost_evidence_unavailable')
            load_intent = self._bundle.runtime.load_intent if reader is None else execution.load
            current = load_intent(expected.intent.intent_id, **reader_args)
            # The runtime compared the business fields to expected before this
            # check. Use the native REST confirmation without inventing fills.
            if current.last_broker_as_of is None or current.last_broker_as_of > observation.account.as_of:
                raise ValueError('unfilled_cancel_broker_confirmation_missing')
            broker = replace(inputs['broker_snapshot'], as_of=current.last_broker_as_of)
            target = replace(inputs['target'], decision_time=now, account_input_ref=observation.input_ref,
                             orders_input_ref=observation.input_ref)
            risk_allows_new_risk = inputs['assessment'].risk_allows_new_risk
            if (inputs['risk'].daily_net_pnl_won is None and self._flat_unfilled_buy_cancel_cut(
                    inputs['policy'], inputs['observation'], inputs['risk'])):
                risk_allows_new_risk = risk.daily_net_pnl_won is not None
            assessment = replace(inputs['assessment'], risk_input_ref='native-risk:' + risk.snapshot_id,
                risk_allows_new_risk=risk_allows_new_risk,
                risk_available_at=risk.observed_at,
                risk_valid_until=min(inputs['policy'].valid_until, risk.observed_at + timedelta(seconds=age)),
                protection_required=inputs['assessment'].protection_required or self._protection_pending() or trigger is not None)
            anchor_intent_id = inputs['policy'].anchor_intent_id
            root = current if anchor_intent_id == current.intent.intent_id else load_intent(anchor_intent_id, **reader_args)
        current_result = evaluate_unfilled_order_management_shadow(inputs['policy'], current, broker,
            target, observation, assessment, root_record=root)
        if (current_result['action'] != 'CANCEL' or current_result['status'] != 'PROPOSED'
                or (current_result['after_cancel'] != plan['result']['after_cancel']
                    and current_result['after_cancel'] != 'ABANDON')):
            raise ValueError('unfilled_cancel_current_assessment_changed')
        proof.update(cancel_assessment_ref=current_result['decision_id'],
                     cancel_risk_snapshot_id=risk.snapshot_id,
                     cancel_risk_reconciliation_revision=risk.reconciliation_revision,
                     cancel_broker_as_of=current.last_broker_as_of.isoformat(),
                     after_cancel=current_result['after_cancel'])




    async def _reprepare_unclaimed_leader_protection(self, control, projection, execution):
        """A checkpoint alone spends no intent; native first-claim fencing still owns submission."""
        selected = self._planned_protection_restore
        workflow, plan, _ = selected
        root = self._leader_plan_root[2] if self._leader_plan_root is not None else None
        notice = self._protection_snapshot() or {}
        if (projection["state"] != "PREPARED_NO_INTENT" or projection["plan_revision"] != plan.expected_revision
                or projection["reserved_quantity"] or projection["unreconciled_sell_quantity"]
                or root is None or root.state not in {OrderState.CANCELLED, OrderState.FILLED}
                or not root.broker_order_id or not 0 < root.detailed_filled_quantity == projection["plan_quantity"]
                or (root.intent.plan.plan_id, root.intent.plan.policy_ref) != (plan.plan_id, plan.policy_ref)
                or notice.get("planned_leader_protection_exit") != workflow
                or "planned_leader_stop" not in notice or self._strategy_inputs.latest_stop_quote is None):
            return
        monitor = self._bundle.monitor
        cut = monitor.latest_automation_execution_cut
        if cut is None or cut["risk"].reconciliation_revision <= workflow["snapshot"]["risk_reconciliation_revision"]:
            if self._leader_plan_reprepare_requested != workflow["workflow_id"]:
                self._leader_plan_reprepare_requested = workflow["workflow_id"]
                self._leader_plan_refresh_quote = self._strategy_inputs.latest_stop_quote
                monitor.request_automation_execution_recovery()
            return
        try:
            await self._submit_leader_plan_protection(control, root, notice, self._now(), execution)
        finally:
            if self._planned_protection_restore is not selected:
                self._planned_protection_readback = None

    def _read_planned_protection(self, execution: ExecutionRepository, *, _reader=None):
        from kiwoom_monitor.application.mock_automation_execution import MockAutomationGateStatus, mock_automation_intent_id
        from kiwoom_monitor.application.mock_automation_risk import PROTECTION_EXIT_VERSION

        workflow, plan, decision = self._planned_protection_restore
        revision = self._bundle.runtime.observation_revision
        if self._planned_protection_readback is not None and self._planned_protection_readback[0] == revision:
            return
        account, run_id = self._bundle.account_ref, self._bundle.run_id
        identity = mock_automation_intent_id(self._spec.spec_id, decision.decision_id)
        head = execution.load_plan_position(account, run_id, decision.symbol, plan, _reader=_reader)
        record, claim = execution.load_protective_execution(identity, _reader=_reader)
        projection = {"intent_id": identity, "state": "PREPARED_NO_INTENT",
                      "plan_id": plan.plan_id, "plan_revision": head.plan.expected_revision,
                      "plan_quantity": head.quantity, "unreconciled_sell_quantity": head.unreconciled_sell_quantity,
                      "reserved_quantity": 0, "detailed_filled_quantity": 0, "order_authorized": False}
        if record is not None:
            gate = self._repository.load_mock_automation_approved_gate(account, identity, _reader=_reader)
            snapshot = workflow["snapshot"]
            intent = record.intent
            decided_at = datetime.fromisoformat(decision.decided_at)
            if (not isinstance(intent, PlannedOrderIntent) or intent.plan != plan or claim is None or gate is None
                    or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
                    or (gate.account_ref, gate.execution_run_id, gate.spec_id, gate.intent_id, gate.strategy_decision_id)
                    != (account, run_id, self._spec.spec_id, identity, decision.decision_id)
                    or (gate.plan, gate.action, gate.symbol, gate.quantity, gate.limit_price, gate.decided_at,
                        gate.control_revision) != (plan, "EXIT", decision.symbol, decision.quantity,
                        decision.signal_reference_price, decided_at, snapshot["control_revision"])
                    or (gate.gate_id, gate.risk_snapshot_id, gate.risk_reconciliation_revision)
                    != (claim["gate_id"], snapshot["risk_snapshot_id"], snapshot["risk_reconciliation_revision"])
                    or (claim["workflow_id"], claim["risk_snapshot_id"], claim["risk_reconciliation_revision"])
                    != (workflow["workflow_id"], gate.risk_snapshot_id, gate.risk_reconciliation_revision)
                    or (intent.environment, intent.account_ref, intent.run_id, intent.side, intent.policy_version,
                        intent.decision_id, intent.symbol, intent.quantity, intent.limit_price, intent.created_at)
                    != ("mock", account, run_id, OrderSide.SELL, PROTECTION_EXIT_VERSION, decision.decision_id,
                        decision.symbol, decision.quantity, decision.signal_reference_price, decided_at)
                    or not 0 <= record.detailed_filled_quantity <= record.filled_quantity <= intent.quantity
                    or head.plan.expected_revision <= plan.expected_revision):
                raise ValueError("planned_protection_native_receipt_changed")
            reserved = (0 if record.state in TERMINAL_ORDER_STATES
                        else intent.quantity - record.detailed_filled_quantity)
            projection.update(state=record.state.value, reserved_quantity=reserved,
                              detailed_filled_quantity=record.detailed_filled_quantity)
        self._planned_protection_readback = ((revision, projection)
                                             if revision == self._bundle.runtime.observation_revision else None)

    def _read_leader_plan_root(self, execution: ExecutionRepository, *, _reader=None):
        pin = self._leader_position_proof
        if pin is None:
            self._leader_plan_root = None
            return
        revision = self._bundle.runtime.observation_revision
        if self._leader_plan_root is not None and self._leader_plan_root[:2] == (pin["entry_intent_id"], revision):
            return
        root = execution.load(pin["entry_intent_id"], _reader=_reader)
        if root is not None:
            intent = root.intent
            if (not isinstance(intent, PlannedOrderIntent) or intent.side is not OrderSide.BUY
                    or (intent.account_ref, intent.run_id, intent.plan.plan_id, intent.plan.policy_ref)
                    != (self._bundle.account_ref, self._bundle.run_id, pin["entry_intent_id"], pin["proof_id"])):
                raise ValueError("leader plan root does not match the restored pin")
            if self._leader_plan_root is None or self._leader_plan_root[0] != pin["entry_intent_id"] or self._leader_plan_deadline is None:
                from kiwoom_monitor.application.order_lifecycle import anchored_unfilled_policy

                reader_args = {} if _reader is None else {"_reader": _reader}
                source = self._original_policy_document("execution_leader_decisions",
                    self._leader_evidence_key(intent.decision_id), **reader_args)["document"]
                signal_until = datetime.fromisoformat(source["snapshot"]["feature_values"][0]["value"]["valid_until"])
                policy = anchored_unfilled_policy(intent, self._spec.unfilled_execution_policy, "BUY_OPPORTUNITY")
                self._leader_plan_deadline = min(signal_until, policy.wait_until)
        self._leader_plan_root = (pin["entry_intent_id"], revision, root)
        if (root is not None and self._spec.unfilled_execution_policy.accepted_order_management is not None
                and root.state in {OrderState.FILLED, OrderState.CANCELLED} and root.detailed_filled_quantity):
            from kiwoom_monitor.application.mock_automation_risk import planned_leader_root_confirmation_time
            from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_legs

            key = (root.intent.intent_id, root.state, root.detailed_filled_quantity,
                   tuple((leg.broker_order_id, leg.state, leg.filled_quantity, leg.remaining_quantity)
                         for leg in execution_broker_legs(root)))
            if self._leader_plan_confirmation is None or self._leader_plan_confirmation[0] != key:
                self._leader_plan_confirmation = (key, planned_leader_root_confirmation_time(root,
                    execution.events(root.intent.intent_id, **({} if _reader is None else {'_reader': _reader}))))
        if root is not None and hasattr(self._config.execution_contract, 'position_steps'):
            self._read_leader_staged_facts(execution, root, revision, _reader=_reader)

    def _read_leader_staged_facts(self, execution, root, revision, *, _reader=None):
        """Recover accepted steps and share material fill facts with current management."""
        from kiwoom_monitor.application.leader_breakout import _id, leader_position_step_key
        from kiwoom_monitor.application.leader_order_management import leader_position_order_confirmation_time
        from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_legs
        args = {} if _reader is None else {'_reader': _reader}
        account, run_id = self._bundle.account_ref, self._bundle.run_id
        head = execution.load_plan_position(account, run_id, root.intent.symbol, root.intent.plan, **args)
        head_key = (head.plan.expected_revision, head.fill_cursor)
        prior = self._leader_staged_facts or {}
        claims = prior.get('claims', ())
        all_keys = tuple(leader_position_step_key(self._config, index, step,
                         context=self._leader_position_proof['context'])
            for index, step in enumerate(self._config.execution_contract.position_steps))
        if prior.get('head_key') != head_key:
            claims = []
            for index, key in enumerate(all_keys):
                row = self._original_policy_document('execution_leader_position_actions',
                    _id('leader-plan-step-claim/v1', [head.plan.plan_id, key]), **args)
                if row is None:
                    break
                claim = row['document']
                if (claim.get('spec_id'), claim.get('run_id'), claim.get('plan_id'), claim.get('policy_ref'),
                    claim.get('index'), claim.get('step_key')) != (
                        self._spec.spec_id, run_id, head.plan.plan_id, head.plan.policy_ref, index, key):
                    raise ValueError('leader_position_action_claim_changed')
                claims.append(claim)
        records, pages, signatures, confirmations = {}, {}, {}, {}
        for identity in (root.intent.intent_id, *(claim['intent_id'] for claim in claims)):
            previous = prior.get('records', {}).get(identity)
            record = (root if identity == root.intent.intent_id else previous
                if previous is not None and previous.state in TERMINAL_ORDER_STATES and prior.get('head_key') == head_key
                else execution.load(identity, **args))
            if (record is None or not isinstance(record.intent, PlannedOrderIntent)
                    or (record.intent.account_ref, record.intent.run_id, record.intent.plan.plan_id, record.intent.plan.policy_ref)
                    != (account, run_id, head.plan.plan_id, head.plan.policy_ref)):
                raise ValueError('leader_position_action_order_missing_or_changed')
            claimed = next((claim for claim in claims if claim['intent_id'] == identity), None)
            if claimed is not None and identity not in prior.get('records', {}):
                gate = self._repository.load_mock_automation_approved_gate(account, identity, **args)
                if (gate is None or gate.version != 'mock_automation_plan_action_gate/v1'
                        or (gate.position_action_ref, gate.strategy_decision_id, gate.action, gate.quantity, gate.plan)
                        != (claimed['position_action_ref'], record.intent.decision_id, claimed['action'],
                            record.intent.quantity, record.intent.plan)):
                    raise ValueError('leader_position_action_approval_changed')
            signature = (record.state, record.detailed_filled_quantity, record.broker_reported_filled_quantity,
                record.amendment.reconciliation_pending if record.amendment is not None else False,
                tuple((leg.broker_order_id, leg.state, leg.filled_quantity, leg.remaining_quantity)
                      for leg in execution_broker_legs(record)))
            events = (prior['pages'][identity] if prior.get('signatures', {}).get(identity) == signature
                      else execution.events(identity, **args))
            confirmed = None
            if (record.state in TERMINAL_ORDER_STATES and record.filled_quantity == record.detailed_filled_quantity
                    and record.filled_quantity == record.broker_reported_filled_quantity
                    and not (record.amendment is not None and record.amendment.reconciliation_pending)):
                confirmed = (prior['confirmations'].get(identity) if prior.get('signatures', {}).get(identity) == signature
                             else leader_position_order_confirmation_time(record, events))
            records[identity], pages[identity], signatures[identity], confirmations[identity] = record, events, signature, confirmed
        keys = tuple(claim['step_key'] for claim in claims)
        state = self._episode_position.state
        merged = (*[key for key in state.emitted_candidate_keys if key not in all_keys], *keys)
        if merged != state.emitted_candidate_keys:
            self._episode_position.replace_strategy_state(replace(state, emitted_candidate_keys=merged),
                position_time=self._position_state_time)
        self._leader_staged_facts = dict(head_key=head_key, revision=revision, claims=tuple(claims), records=records,
            pages=pages, signatures=signatures, confirmations=confirmations,
            entry_notional=sum(event['quantity'] * event['price'] for identity, events in pages.items()
                if records[identity].intent.side is OrderSide.BUY for event in events if event['event_type'] == 'FILL'),
            confirmed_at=max(confirmations.values()) if confirmations and all(confirmations.values()) else None)

    def _apply_fills_and_read_control(self, execution: ExecutionRepository):
        with self._repository.mock_automation_read_scope() as reader:
            reader_args = {} if reader is None else {'_reader': reader}
            self._episode_position.apply_new_fills(
                execution, leader=self._leader, config=self._config,
                position_time=self._position_state_time, **reader_args,
            )
            if self._planned_protection_restore is not None:
                self._read_planned_protection(execution, **reader_args)
            if self._leader_plan_mode:
                self._read_leader_plan_root(execution, **reader_args)
            # Read control after all FILLs have been applied. The read scope
            # releases its cursor before any input or checkpoint work starts.
            return self._repository.load_mock_automation_control(self._bundle.account_ref, **reader_args)

    async def advance_once(self, *, limit: int = 1000) -> int:
        if not self._unfilled_preflight_lock.acquire(blocking=False):
            raise RuntimeError("unfilled_preflight_already_in_progress")
        try:
            poll_started = perf_counter()
            # FILL reads mutate checkpoint cursor/state under every profile.
            # Close must join them before taking its final checkpoint snapshot.
            execution = self._bundle.repository
            if (type(execution) is ExecutionRepository and execution.uses_store(self._store)
                    and callable(getattr(self._store, 'mock_automation_read_scope', None))):
                control = await drain_thread_call(self._apply_fills_and_read_control, execution)
            else:
                await drain_thread_call(
                    self._episode_position.apply_new_fills, execution,
                    leader=self._leader, config=self._config, position_time=self._position_state_time,
                )
                native_call = (drain_thread_call if self._input_progress.recovering
                               or self._config.session_exit_policy else asyncio.to_thread)
                control = await native_call(
                    self._repository.load_mock_automation_control, self._bundle.account_ref,
                )
            if (control is None or not self._matches_control(control)
                    or getattr(control, "execution_run_id", self._bundle.run_id) != self._bundle.run_id):
                status = {
                    **(self._status if self._input_progress.recovering else {}),
                    "state": "BLOCKED", "reason": "automation_control_missing_or_replaced",
                    "orders_enabled": False,
                }
                if status != self._status or self._checkpoint.pending:
                    self._status = status
                    await drain_thread_call(self._save_checkpoint)
                return 0
            if self._planned_protection_restore is not None and self._protection_pending():
                stopped = control.desired_state.value != "RUNNING"
                if stopped and self._leader:
                    self._strategy_inputs.reset("user_stopped")
                projection = self._planned_protection_readback
                if not stopped and self._leader_plan_mode:
                    root = self._leader_plan_root[2] if self._leader_plan_root is not None else None
                    if await self._manage_current_leader_order(control, root, self._protection_snapshot() or {}):
                        return 0
                if (self._leader_plan_mode and not stopped and projection is not None
                        and projection[1]["state"] == "PREPARED_NO_INTENT"):
                    await self._reprepare_unclaimed_leader_protection(control, projection[1], execution)
                    projection = self._planned_protection_readback
                status = {"state": "STOPPED" if stopped else "PROTECTION_REVIEW_REQUIRED",
                          "reason": "planned_protection_readback", "orders_enabled": False,
                          "planned_protection": projection[1] if projection else {"state": "WAIT_NATIVE_READBACK"}}
                if status != self._status or self._checkpoint.pending:
                    self._status = status
                    await drain_thread_call(self._save_checkpoint)
                return 0
            if self._minute_management is not None:
                if self._checkpoint.pending or self._protection_pending() or self._minute_management.checkpoint_pending:
                    await drain_thread_call(self._save_checkpoint)
                count = await self._minute_management.advance(control, limit=limit,
                    allow_entry=lambda: not self._closing and not self._protection_pending() and self._allows_entry(control),
                    protection_notice=self._protection_snapshot())
                self._status = dict(self._minute_management.status)
                await drain_thread_call(self._save_checkpoint)
                return count
            if control.desired_state.value != "RUNNING":
                if self._leader:
                    self._strategy_inputs.reset("user_stopped")
                if (self._config.session_exit_policy and self._session_clock is not None
                        and control.reason.startswith("session_clock:")):
                    await self._clock_call(control)
                    if self._checkpoint.pending:
                        await drain_thread_call(self._save_checkpoint)
                    return 0
                status = {
                    **(self._status if self._input_progress.recovering else {}),
                    "state": "STOPPED", "reason": "user_stopped", "orders_enabled": False,
                }
                if status != self._status or self._checkpoint.pending:
                    self._status = status
                    await drain_thread_call(self._save_checkpoint)
                return 0

            if self._leader_plan_mode:
                root = self._leader_plan_root[2] if self._leader_plan_root is not None else None
                if await self._manage_leader_plan(control, root):
                    return 0
                return await self._run_leader_page(control, limit)

            unfilled_ready = ((not self._input_progress.bootstrap_pending and not self._input_progress.recovering)
                    or self._unfilled_registration is not None
                       and (self._protection_pending() or self._unfilled_registration['purpose'] == 'PROTECTIVE_EXIT'))
            if unfilled_ready:
                await self._produce_native_unfilled_inputs(control)
            if unfilled_ready and await self._unfilled_call():
                await self._pause_for_protection_review()
                return 0
            if await self._pause_for_protection_review():
                return 0
            if self._leader:
                return await self._run_leader_page(control, limit)
            load_started = perf_counter()
            if self._input_progress.bootstrap_pending:
                if await self._clock_call(control):
                    return 0
                await drain_thread_call(
                    self._input_progress.bootstrap, self._store, self._strategy_inputs,
                    leader=self._leader, publish_status=lambda value: setattr(self, "_status", value),
                    checkpoint=self._save_checkpoint,
                )
                return 0  # Never evaluate historical seed or dispatch its orders.
            if self._input_progress.recovering:
                if await self._clock_call(control):
                    return 0
                await drain_thread_call(
                    self._input_progress.repair, self._store, self._strategy_inputs, limit,
                    checkpoint=self._save_checkpoint,
                )
                return 0
            if self._checkpoint.pending:
                # Persist the consumed prefix before loading any subsequent input.
                # Fills and current control were refreshed above; never re-evaluate it.
                await drain_thread_call(self._save_checkpoint)
            observations = await asyncio.to_thread(
                self._store.load_observation_revisions_after,
                self._input_progress.cursor, INPUT_KINDS, limit,
            )
            load_ms = round((perf_counter() - load_started) * 1000)
            if await self._clock_call(control, observations):
                return 0
            max_processing_ms = 0
            max_bar_age_ms: int | None = None
            bar_count = 0
            future_bar_count = 0
            last_bar_age_ms = self._latency.get("last_bar_age_ms") if self._latency else None
            last_bar_processed_at = self._latency.get("last_bar_processed_at") if self._latency else None
            consumed = 0
            for observation in observations:
                if await self._pause_for_protection_review():
                    break
                if await self._clock_call(control, observations):
                    break
                observation_started = perf_counter()
                if await self._consume(observation) is False:
                    break
                self._input_progress.advance_consumed(observation)
                consumed += 1
                await drain_thread_call(self._save_checkpoint)
                max_processing_ms = max(
                    max_processing_ms, round((perf_counter() - observation_started) * 1000),
                )
                if observation.get("kind") == "minute_bar":
                    bar_count += 1
                    processed_at = self._now()
                    last_bar_processed_at = processed_at.isoformat()
                    try:
                        available_at = datetime.fromisoformat(str(observation.get("available_at", "")))
                        if available_at.tzinfo is None:
                            raise ValueError("minute bar availability lacks timezone")
                        age_ms = round((processed_at - available_at).total_seconds() * 1000)
                    except ValueError:
                        age_ms = -1
                    if age_ms < 0:
                        future_bar_count += 1
                        last_bar_age_ms = None
                    else:
                        last_bar_age_ms = age_ms
                        max_bar_age_ms = max(age_ms, max_bar_age_ms or 0)
            self._latency = {
                "last_poll_completed_at": self._now().isoformat(),
                "last_poll_duration_ms": round((perf_counter() - poll_started) * 1000),
                "load_duration_ms": load_ms,
                "observation_count": consumed,
                "minute_bar_count": bar_count,
                "max_observation_processing_ms": max_processing_ms,
                "max_minute_bar_age_ms": max_bar_age_ms,
                "future_or_invalid_bar_time_count": future_bar_count,
                "last_bar_age_ms": last_bar_age_ms,
                "last_bar_processed_at": last_bar_processed_at,
            }
            return consumed
        finally:
            self._unfilled_preflight_lock.release()

    async def _clock_call(self, control, observations=()) -> bool:
        if self._leader:
            return False  # New session exits require the same fresh actual 0B cut.
        if not self._config.session_exit_policy:
            return False
        # A close joins the whole broker/clock operation, including its native
        # writes, rather than leaving a shielded gateway cancellation behind.
        task = asyncio.create_task(self._poll_session_clock(control, observations))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled() and task.exception() is not None:
                logger.error("session clock failed while draining", exc_info=task.exception())
            raise

    def _save_session_clock(self) -> None:
        self._store.upsert_documents(CLOCK_COLLECTION, [{
            "owner": self._bundle.account_ref, "key": self._checkpoint_key,
            "document": self._session_clock,
        }])

    async def _clock_status(self, state: str, reason: str) -> None:
        self._session_clock.update(phase=state, reason=reason)
        self._status = {
            "state": "RUNNING" if state == "COMPLETED" else state,
            "reason": reason, "orders_enabled": False,
            "pending_intent_id": self._session_clock.get("pending_intent_id", ""),
            "updated_at": self._bundle.runtime.server_now().isoformat(),
        }
        await drain_thread_call(self._save_session_clock)

    async def _stop_session_clock(self, control, reason: str) -> None:
        stop_reason = "session_clock:" + reason
        self._session_clock["stop_reason"] = stop_reason
        await drain_thread_call(self._save_session_clock)
        stop = await drain_thread_call(
            emergency_stop_mock_automation, self._repository, self._bundle.runtime,
            account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id,
            stopped_at=self._bundle.runtime.server_now(), reason=stop_reason,
            expected_control_revision=control.control_revision,
        )
        self._session_clock["stop_revision_id"] = stop.revision_id
        await self._clock_status("STOPPED", reason)

    async def _poll_session_clock(self, control, observations) -> bool:
        now = self._bundle.runtime.server_now()
        active = await drain_thread_call(
            self._bundle.repository.active_intents,
            "mock", self._bundle.account_ref, self._bundle.run_id,
        )
        active = self._own_active_records(active)
        assessment = session_clock_assessment(
            self._config, self._episode_position.state, now, active, management_preflight=self._unfilled_attempt_plan or self._unfilled_preflight,
        )
        stopped = control.desired_state.value != "RUNNING"
        if not stopped and self._session_clock is not None and (
            self._session_clock["control_revision"] != control.control_revision
            or (self._session_clock.get("phase") == "COMPLETED" and assessment["reasons"])
            or (self._session_clock.get("decision") is not None and self._episode_position.state.status == "open"
                and self._session_clock.get("position_opened_at") != self._episode_position.state.opened_at)
        ):
            # Only an explicit resume (with its existing fresh-recovery gate)
            # can begin a new workflow after STOP; actual subsequent positions
            # in a running control receive their own Decision and cancel keys.
            self._session_clock = None
        if not assessment["reasons"] and not stopped:
            if self._session_clock is not None and self._session_clock.get("phase") != "COMPLETED":
                await self._clock_status("COMPLETED", "actual_exit_or_order_reconciliation_complete")
            return False
        if self._session_clock is None:
            self._session_clock = {
                "version": SESSION_CLOCK_VERSION, "account_ref": self._bundle.account_ref,
                "spec_id": self._spec.spec_id, "execution_run_id": self._bundle.run_id,
                "candidate_package_hash": self._spec.candidate_package_hash,
                "control_revision": control.control_revision,
                "cancel_attempted_intent_ids": [], "assessment": assessment,
                "position_opened_at": self._episode_position.state.opened_at,
            }
        self._session_clock["assessment"] = assessment
        await drain_thread_call(self._save_session_clock)
        if stopped or any(reason in assessment["reasons"] for reason in (
            "session_deadline_passed", "order_validity_expired", "position_time_from_future",
        )):
            if not stopped or not self._session_clock.get("stop_revision_id"):
                await self._stop_session_clock(control, ";".join(assessment["reasons"]) or "recovery_required")
            else:
                await self._clock_status("STOPPED", self._session_clock.get("reason", "recovery_required"))
            await self._cancel_clock_orders(all_orders=True)
            return True
        if active:
            await self._clock_status("WAITING_ORDER", "clock_exit_waits_for_broker_order")
            await self._cancel_clock_orders(all_orders=False)
            return True
        try:
            recovery, risk = await self._refresh_recovery_risk()
            await drain_thread_call(
                self._episode_position.apply_new_fills, self._bundle.repository,
                leader=self._leader, config=self._config, position_time=self._position_state_time,
            )
            active = await drain_thread_call(
                self._bundle.repository.active_intents,
                "mock", self._bundle.account_ref, self._bundle.run_id,
            )
            active = self._own_active_records(active)
            if risk is None or not risk.reconciliation_complete or not self._episode_position.matches_risk(risk):
                raise ValueError("clock_broker_reconciliation_unverified")
            if active:
                await self._clock_status("WAITING_ORDER", "clock_exit_waits_for_broker_order")
                await self._cancel_clock_orders(all_orders=False)
                return True
            refreshed = session_clock_assessment(
                self._config, self._episode_position.state, risk.observed_at, active,
            )
            if not refreshed["exit_required"]:
                if self._episode_position.state.status == "open":
                    raise ValueError("clock_recovery_predates_exit_due")
                await self._clock_status("COMPLETED", "actual_exit_fill_confirmed")
                return True
            bar = self._strategy_inputs.clock_price_frame(
                self._episode_position.state.symbol, risk.observed_at, observations,
            )
            if self._input_progress.bootstrap_pending or self._input_progress.recovering:
                raise ValueError("clock_price_frames_not_ready")
            decision, snapshot = clock_exit_decision(
                spec_id=self._spec.spec_id, run_id=self._bundle.run_id,
                state=self._episode_position.state, assessment=refreshed, bar=bar, now=risk.observed_at,
                maximum_age_seconds=self._spec.maximum_data_gap_seconds,
            )
            if self._session_clock.get("decision") is not None:
                body = dict(self._session_clock["decision"])
                body["reasons"] = tuple(body["reasons"])
                body["constraints"] = tuple(body["constraints"])
                decision = StrategyDecision(**body)
            else:
                self._session_clock.update(decision=decision.to_dict(), snapshot=snapshot)
                await drain_thread_call(self._save_session_clock)
            await drain_thread_call(self._record_recovery, recovery, risk)
            if await self._pause_for_protection_review():
                return True
            gate, record, _ = await drain_thread_call(self._dispatch_original_and_register, decision, recovery, risk)
            if record is None or record.state in TERMINAL_ORDER_STATES:
                raise ValueError("clock_exit_gate_blocked:" + ";".join(gate.reasons))
            self._session_clock["pending_intent_id"] = record.intent.intent_id
            await self._clock_status("ORDER_SUBMITTED", "clock_exit_submitted")
        except Exception as error:
            latest = await drain_thread_call(
                self._repository.load_mock_automation_control, self._bundle.account_ref,
            )
            # Do not stop a replacement control or override a concurrent user stop.
            if latest == control:
                await self._stop_session_clock(control, str(error))
                await self._cancel_clock_orders(all_orders=True)
            else:
                await self._clock_status("BLOCKED", "clock_control_changed")
        return True

    async def _cancel_clock_orders(self, *, all_orders: bool) -> None:
        active = await drain_thread_call(
            self._bundle.repository.active_intents,
            "mock", self._bundle.account_ref, self._bundle.run_id,
        )
        active = self._own_active_records(active)
        if not any(
            record.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
            and (all_orders or record.intent.side is OrderSide.BUY)
            and record.intent.intent_id not in self._session_clock["cancel_attempted_intent_ids"]
            for record in active
        ):
            # UNKNOWN/CANCEL_PENDING reconciliation is already owned by the
            # monitor. Do not flood its broker with full queries every idle poll.
            self._session_clock["unresolved_intent_ids"] = [record.intent.intent_id for record in active]
            await drain_thread_call(self._save_session_clock)
            return
        try:
            recovery, risk = await self._refresh_recovery_risk()
            await drain_thread_call(
                self._episode_position.apply_new_fills, self._bundle.repository,
                leader=self._leader, config=self._config, position_time=self._position_state_time,
            )
            now = self._bundle.runtime.server_now()
            if (risk is None
                    or risk.execution_run_id != self._bundle.run_id
                    or risk.account_ref != self._bundle.account_ref
                    or risk.account_as_of != recovery.account.as_of
                    or recovery.account.account_ref != self._bundle.account_ref
                    or recovery.account.as_of > now
                    or (now - recovery.account.as_of).total_seconds() > self._spec.maximum_data_gap_seconds
                    or risk.observed_at > now
                    or (now - risk.observed_at).total_seconds() > self._spec.maximum_data_gap_seconds):
                raise ValueError("clock_cancel_recovery_unverified")
            active = await drain_thread_call(
                self._bundle.repository.active_intents,
                "mock", self._bundle.account_ref, self._bundle.run_id,
            )
            active = self._own_active_records(active)
            confirmed = {order.broker_order_id: order for order in recovery.orders}
            for record in active:
                order = confirmed.get(record.broker_order_id)
                if (record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
                        or (not all_orders and record.intent.side is not OrderSide.BUY)
                        or record.intent.intent_id in self._session_clock["cancel_attempted_intent_ids"]
                        or order is None or order.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
                        or order.as_of > now or (now - order.as_of).total_seconds() > self._spec.maximum_data_gap_seconds):
                    continue
                latest = await drain_thread_call(
                    self._repository.load_mock_automation_control, self._bundle.account_ref,
                )
                if (latest is None or not self._matches_control(latest)
                        or latest.execution_run_id != self._bundle.run_id
                        or (latest.desired_state.value != "RUNNING"
                            and not latest.reason.startswith("session_clock:"))):
                    raise ValueError("clock_cancel_control_changed")
                # Persist before the one-shot transport. A crash/unknown response
                # cannot cause a second cancellation on the next poll or restart.
                self._session_clock["cancel_attempted_intent_ids"].append(record.intent.intent_id)
                await drain_thread_call(self._save_session_clock)
                await self._bundle.gateway.cancel(record.intent.intent_id)
        except Exception as error:
            self._session_clock["cancel_error"] = str(error)
        active = await drain_thread_call(
            self._bundle.repository.active_intents,
            "mock", self._bundle.account_ref, self._bundle.run_id,
        )
        self._session_clock["unresolved_intent_ids"] = [record.intent.intent_id for record in active]
        await drain_thread_call(self._save_session_clock)

    async def _consume(self, observation: Mapping[str, Any]) -> bool | None:
        evaluation_bar = self._strategy_inputs.accept_observation(observation)
        if evaluation_bar is None:
            return
        await drain_thread_call(
            self._episode_position.apply_new_fills, self._bundle.repository,
            leader=self._leader, config=self._config, position_time=self._position_state_time,
        )
        active = await asyncio.to_thread(
            self._bundle.repository.active_intents,
            "mock", self._bundle.account_ref, self._bundle.run_id,
        )
        if active:
            registration=self._unfilled_registration
            if (registration is not None and registration['purpose']=='TAKE_PROFIT'
                    and self._episode_position.state.status=='open' and self._episode_position.state.symbol==evaluation_bar.code
                    and not self._protection_pending()):
                # Same registered pure family, once for this actual bar. Its
                # result grants no orders and never changes fill-owned state.
                evaluation=self._strategy_inputs.evaluate_bar(family=self._family,
                    run_id=self._bundle.run_id,evaluation_bar=evaluation_bar,config=self._config,state=self._episode_position.state)
                decision=evaluation.decision
                if decision.symbol==registration['root_intent']['symbol']:
                    self._unfilled_judgment=decision.to_dict()
                    self._unfilled_source_pending=True
                    if decision.final_action=='EXIT' and set(decision.reasons)&{'stop_loss_reached','max_hold_reached','session_exit_due'}:
                        self.request_protection_review(reason='registered_strategy_protective_exit',
                            input_ref=decision.decision_id,requested_at=self._bundle.runtime.server_now())
            self._status = {
                "state": "WAITING_ORDER", "reason": "active_intent_reconciliation",
                "pending_intent_id": active[0].intent.intent_id, "orders_enabled": False,
                "updated_at": evaluation_bar.available_at,
            }
            return
        if await self._pause_for_protection_review():
            return False
        entry_market = (self._prepare_entry_market(datetime.fromisoformat(evaluation_bar.available_at))
                        if self._episode_position.state.status != "open" else None)
        evaluation = self._strategy_inputs.evaluate_bar(
            family=self._family, run_id=self._bundle.run_id, evaluation_bar=evaluation_bar,
            config=self._config, state=self._episode_position.state,
        )
        self._episode_position.replace_strategy_state(evaluation.state, position_time=self._position_state_time)
        decision = evaluation.decision
        if decision.final_action not in {"ENTER", "EXIT"}:
            self._status = {
                "state": "RUNNING", "reason": ";".join(decision.reasons),
                "last_decision_id": decision.decision_id, "orders_enabled": False,
                "updated_at": decision.decided_at,
            }
            return

        if decision.final_action == "ENTER":
            self._observe_entry_market(decision.symbol, entry_market)
        recovery, risk = await self._refresh_recovery_risk()
        if risk is None:
            raise RuntimeError("MOCK_AUTOMATION_RISK_MISSING")
        if not self._episode_position.matches_risk(risk):
            self._status = {
                "state": "BLOCKED", "reason": "strategy_state_risk_mismatch",
                "last_decision_id": decision.decision_id, "orders_enabled": False,
                "updated_at": decision.decided_at,
            }
            return
        await asyncio.to_thread(self._record_recovery, recovery, risk)
        if decision.final_action == "ENTER" and self._config.session_exit_policy:
            reason = session_entry_time_rejection(self._config, self._bundle.runtime.server_now())
            if reason:
                self._status = {
                    "state": "BLOCKED", "reason": "clock_entry:" + reason,
                    "last_decision_id": decision.decision_id, "orders_enabled": False,
                    "updated_at": self._bundle.runtime.server_now().isoformat(),
                }
                return
        if await self._pause_for_protection_review():
            return False
        try:
            gate, record, _ = await drain_thread_call(self._dispatch_original_and_register, decision, recovery, risk)
        except AutomationViBuyBlocked as error:
            self._status = {"state": "BLOCKED", "reason": str(error), "last_decision_id": decision.decision_id,
                            "orders_enabled": False, "updated_at": decision.decided_at}
            return
        submitted = record is not None and record.state is not OrderState.REJECTED
        self._status = {
            "state": "ORDER_SUBMITTED" if submitted else "BLOCKED",
            "reason": ";".join(gate.reasons) if gate.reasons else "approved" if submitted else "order_rejected",
            "last_decision_id": decision.decision_id,
            "last_gate_id": gate.gate_id,
            "pending_intent_id": record.intent.intent_id if record is not None else "",
            "orders_enabled": False,
            "updated_at": decision.decided_at,
        }

    def _restore_or_bootstrap(self, document) -> None:
        if isinstance(document, Mapping) and (
            document.get("version") == RUNNER_CHECKPOINT_VERSION
            and document.get("spec_id") == self._spec.spec_id
            and document.get("execution_run_id") == self._bundle.run_id
            and document.get("candidate_package_hash") == self._spec.candidate_package_hash
        ):
            if document.get("delivery_protocol") not in ((None, "leader_0B/v1") if self._leader else (None, OBSERVATION_DELIVERY_PROTOCOL)):
                raise RuntimeError("unsupported_mock_observation_delivery_protocol")
            if self._leader and document.get("leader_position_proof") is not None:
                validate_leader_position_proof(document["leader_position_proof"], self._config, self._spec, self._bundle.run_id)
                self._leader_position_proof = deepcopy(document["leader_position_proof"])
            # Never silently discard a replacement reservation and reset its
            # anchor/budget through the legacy malformed-checkpoint bootstrap.
            registration = document.get('unfilled_registration')
            protection_exit=document.get('protection_exit')
            if protection_exit is not None:
                from kiwoom_monitor.application.mock_automation_risk import validate_mock_protection_exit
                validate_mock_protection_exit(protection_exit,self._spec,self._bundle.run_id,
                    selected_management_context=self._protection_policy_context(protection_exit['snapshot']['request_trigger']))
                self._protection_exit=deepcopy(protection_exit)
            if registration is not None:
                self._validate_unfilled_registration(registration)
                self._unfilled_registration = deepcopy(registration)
            preflight = document.get("unfilled_management_preflight")
            if preflight is not None:
                validate_unfilled_management_preflight(preflight)
                scope = preflight["anchor"]["scope"]
                if (scope["account_ref"] != self._bundle.account_ref
                        or scope["execution_run_id"] != self._bundle.run_id
                        or scope["spec_id"] != self._spec.spec_id
                        or scope["candidate_package_hash"] != self._spec.candidate_package_hash):
                    raise ValueError("unfilled_preflight_checkpoint_scope_mismatch")
                self._unfilled_preflight = deepcopy(preflight)
            attempt_plan = document.get('unfilled_execution_preflight')
            if attempt_plan is not None:
                validate_unfilled_attempt_plan(attempt_plan)
                if preflight is None or attempt_plan['anchor'] != preflight['anchor']:
                    raise ValueError('unfilled_native_plan_checkpoint_anchor_mismatch')
                self._unfilled_attempt_plan = deepcopy(attempt_plan)
            cue = document.get('protective_cancel_recovery')
            if cue is not None:
                root_id = attempt_plan['anchor']['root_intent']['intent_id'] if attempt_plan else None
                policy = (attempt_plan or {}).get('anchor',{}).get('policy',{})
                end = min(datetime.fromisoformat(policy[name]) for name in ('valid_until','protection_deadline')) if policy else None
                ids = (root_id,*(child['intent']['intent_id'] for child in attempt_plan['children'])) if attempt_plan else ()
                if (attempt_plan is None or cue.get('version') != 'protective-cancel-recovery/v1'
                        or cue.get('root_intent_id') != root_id or cue.get('managed_intent_id') not in ids
                        or policy['purpose'] != 'PROTECTIVE_EXIT'
                        or cue.get('policy_id') != self._spec.unfilled_execution_policy.policy_id
                        or cue.get('state') not in {'WATCH','RETRY_READY','WAIT_RECONCILIATION','RESOLVED','ESCALATE'}
                        or any(type(cue.get(key)) is not int or cue[key]<0 for key in ('cancel_claims','retries_used'))
                        or cue.get('next_check_at') is not None and datetime.fromisoformat(cue['next_check_at'])>end
                        or cue.get('order_authorized') is not False):
                    raise ValueError('invalid_protective_cancel_recovery_checkpoint')
                # Scheduling is a cue only. Every retry reloads native events,
                # current account/Risk and the same transaction claim fence.
                self._cancel_recovery = deepcopy(cue)
            notice = document.get("protection_review")
            if notice is not None:
                self._validate_protection_review(notice)
                if "planned_leader_protection_exit" in notice:
                    from kiwoom_monitor.application.mock_automation_risk import read_planned_leader_protection_checkpoint

                    workflow = notice["planned_leader_protection_exit"]
                    plan, decision = read_planned_leader_protection_checkpoint(
                        workflow, notice, self._spec, self._bundle.run_id,
                    )
                    self._planned_protection_restore = (deepcopy(workflow), plan, decision)
                    self._planned_protection_readback = None
                self._protection_review = deepcopy(notice)
                if not self._restored_planned_resume_matches(notice):
                    # A checkpoint ACK alone cannot enable entry after restart.
                    self._protection_review["state"] = "PENDING"
                    for key in ("resolved_risk_snapshot_id", "resolved_risk_revision", "planned_zero_fill_root",
                                "resume_from_control_revision", "planned_review_resume_id"):
                        self._protection_review.pop(key, None)
            try:
                self._input_progress.restore_cursor(document.get("input_cursor", 0))
                self._episode_position.restore_fill_cursor(document.get("fill_cursor", 0))
                self._checkpoint.restore_revision(document.get("checkpoint_revision", 0))
                self._episode_position.restore_strategy_state(document.get("strategy_state", {}))
                self._strategy_inputs.restore_bars(document.get("bars", ()))
                self._strategy_inputs.restore_universes(document.get("universes", ()))
                self._episode_position.restore_seen_fill_ids(document.get("seen_fill_ids", ()))
                self._status = dict(document.get("status", self._status))
                if cue is not None and cue["state"] == "RETRY_READY":
                    # A native cancel may have committed before its ACK and the
                    # new checkpoint arrived. Restore the spent claim first.
                    events = self._cancel_lineage_events()
                    record = self._bundle.runtime.load_intent(cue["managed_intent_id"])
                    if record is None:
                        raise ValueError("protective_cancel_intent_missing")
                    frozen = self._spec.unfilled_execution_policy
                    original_policy = anchored_unfilled_policy(
                        unfilled_plan_intent(attempt_plan["anchor"]["root_intent"]), frozen, policy["purpose"],
                    )
                    self._set_cancel_recovery(protective_cancel_recovery(
                        record, events, original_policy, frozen, self._bundle.runtime.server_now(),
                    ), events, original_policy)
                if protection_exit is not None and self._status.get('first_protection_exit_state')=='LIQUIDATION_CONFIRMED':
                    self._status['first_protection_exit_state']='REVALIDATION_REQUIRED'
                    self._status.pop('protection_flat_risk_snapshot_id',None)
                self._input_progress.restore_metadata(
                    document, leader=self._leader, inputs=self._strategy_inputs,
                )
                return
            except (TypeError, ValueError):
                if preflight is not None or attempt_plan is not None or notice is not None:
                    raise RuntimeError("unfilled_preflight_checkpoint_recovery_required")
                logger.warning("invalid automatic runner checkpoint ignored", exc_info=True)
        self._episode_position.apply_new_fills(
            self._bundle.repository, leader=self._leader, config=self._config,
            position_time=self._position_state_time,
        )
        self._input_progress.bootstrap(
            self._store, self._strategy_inputs, leader=self._leader,
            publish_status=lambda value: setattr(self, "_status", value), checkpoint=self._save_checkpoint,
        )

    def _read_zero_fill_plan_receipt(self, recovery=None):
        from kiwoom_monitor.application.mock_automation_risk import planned_zero_fill_root_receipt

        pin = self._leader_position_proof
        if pin is None:
            raise ValueError("planned_resume_zero_fill_root_unconfirmed")
        account, run_id = self._bundle.account_ref, self._bundle.run_id
        execution = self._bundle.repository
        with self._repository.mock_automation_read_scope() as reader:
            root = execution.load(pin["entry_intent_id"], _reader=reader)
            gate = self._repository.load_mock_automation_approved_gate(account, pin["entry_intent_id"], _reader=reader)
            events = execution.events(pin["entry_intent_id"], _reader=reader)
            receipt = planned_zero_fill_root_receipt(
                root, gate, pin, events, account_ref=account, run_id=run_id, spec_id=self._spec.spec_id,
            )
            head = execution.load_plan_position(account, run_id, root.intent.symbol, root.intent.plan, _reader=reader)
            if (head.quantity or head.fill_cursor or head.unreconciled_sell_quantity
                    or head.plan.expected_revision <= root.intent.plan.expected_revision + 1):
                raise ValueError("planned_resume_zero_fill_head_unconfirmed")
        if recovery is not None and not any(
            (order.account_ref, order.broker_order_id, order.symbol, order.state, order.as_of)
            == (account, root.broker_order_id, root.intent.symbol, OrderState.CANCELLED, root.last_broker_as_of)
            and not (order.filled_quantity or order.remaining_quantity or order.fills) for order in recovery.orders
        ):
            raise ValueError("planned_resume_zero_fill_broker_order_unconfirmed")
        return receipt

    def _restored_planned_resume_matches(self, notice) -> bool:
        if not self._leader_plan_mode or notice["state"] != "RESOLVED_FLAT" or not notice.get("planned_review_resume_id"):
            return False
        from kiwoom_monitor.application.mock_automation_risk import planned_review_resume_id

        control = self._repository.load_mock_automation_control(self._bundle.account_ref)
        selection = control.account_selection if control is not None else None
        retained_reader = getattr(selection, 'retained_admission_ref', None)
        retained = retained_reader(self._spec.spec_id) if callable(retained_reader) else None
        receipt = (getattr(control, 'planned_review_resume_ids', {}).get(self._spec.spec_id)
                   if control is not None and self._member_admission is not None
                   else control.planned_review_resume_id if control is not None else None)
        if (receipt is None and control is not None and retained is not None
                and retained.original_selection_id is None
                and self._selection_matches_admission(selection)):
            receipt = control.planned_review_resume_id
        revision_matches = (control is not None and (
            control.control_revision == notice['resume_from_control_revision'] + 1
            or retained is not None and self._selection_matches_admission(selection)
            and control.control_revision > notice['resume_from_control_revision'] + 1))
        linked = (control is not None and control.desired_state.value == "RUNNING" and not control.entry_resume_required
                and self._matches_control(control)
                and revision_matches
                and receipt == notice["planned_review_resume_id"] == planned_review_resume_id(notice))
        if not linked:
            return False
        if self._planned_protection_restore is None:
            try:
                return self._read_zero_fill_plan_receipt() == notice.get("planned_zero_fill_root")
            except ValueError:
                return False
        self._read_planned_protection(self._bundle.repository)
        readback = self._planned_protection_readback
        return (readback is not None and readback[1]["state"] == "FILLED"
                and all(readback[1][name] == 0 for name in ("plan_quantity", "reserved_quantity", "unreconciled_sell_quantity")))

    def _save_checkpoint(self, *, recovery=None, force: bool = False, protection_review=None) -> None:
        # The proposed flat proof may be persisted while the RAM cue stays
        # armed until ACK and post-ACK owner/Risk checks have all succeeded.
        notice = deepcopy(protection_review) if protection_review is not None else self._protection_snapshot()
        incomplete = self._input_progress.checkpoint_incomplete(recovery=recovery)
        if incomplete and notice is None:
            return  # Fills remain in their durable ledger and will be reapplied on restart.
        if recovery is not None and not recovery.complete:
            raise RuntimeError("cannot_checkpoint_partial_frame_recovery")
        bar_frames, universe_frames = ((recovery.bars.values(), recovery.universes)
            if recovery is not None else self._strategy_inputs.frames_for_checkpoint())
        position = self._episode_position.checkpoint_fields()
        content = {
            "version": RUNNER_CHECKPOINT_VERSION,
            "account_ref": self._bundle.account_ref,
            "spec_id": self._spec.spec_id,
            "candidate_package_hash": self._spec.candidate_package_hash,
            "execution_run_id": self._bundle.run_id,
            "input_cursor": self._input_progress.cursor,
            "fill_cursor": position["fill_cursor"],
            "pending_intent_id": str(self._status.get("pending_intent_id", "")),
            "strategy_state": position["strategy_state"],
            "seen_fill_ids": position["seen_fill_ids"],
            "universes": [asdict(value) for value in universe_frames],
            "bars": [_checkpoint_bar_document(value) for value in bar_frames],
            "status": deepcopy(self._status),
        }
        if self._leader:
            content["leader_position_proof"] = deepcopy(self._leader_position_proof)
        content.update(self._input_progress.checkpoint_protocol_fields(recovery=recovery))
        if self._minute_management is not None:
            content['minute_management' if self._panic_plan_mode else 'close_management'] = self._minute_management.checkpoint_fields()
        if self._unfilled_preflight is not None:
            content["unfilled_management_preflight"] = deepcopy(self._unfilled_preflight)
        if self._unfilled_registration is not None:
            content['unfilled_registration'] = deepcopy(self._unfilled_registration)
        if self._cancel_recovery is not None:
            content['protective_cancel_recovery'] = deepcopy(self._cancel_recovery)
        if self._protection_exit is not None:
            content['protection_exit']=deepcopy(self._protection_exit)
        if self._unfilled_attempt_plan is not None:
            content['unfilled_execution_preflight'] = deepcopy(self._unfilled_attempt_plan)
        if notice is not None:
            content["protection_review"] = notice
            if incomplete:
                self._input_progress.project_incomplete_checkpoint(content)
        self._checkpoint.commit(
            content, self._store,
            account_ref=self._bundle.account_ref, now_provider=self._now, key=self._checkpoint_key, force=force,
        )
        if self._minute_management is not None:
            was_pending = self._minute_management.checkpoint_pending
            self._minute_management.acknowledge_checkpoint(content['minute_management' if self._panic_plan_mode else 'close_management'])
            if was_pending and not self._minute_management.checkpoint_pending:
                notify = self._wake_notification()
                if notify is not None: notify()
