"""Persistent NAS runner for one admitted automatic mock-account strategy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from copy import deepcopy
from .database_observation_readers import OBSERVATION_DELIVERY_PROTOCOL
from .observation_frame_recovery import (
    ObservationFrameRecovery, drain_thread_call, trim_bar_frames, trim_universe_frames,
)
from time import perf_counter
from threading import Lock
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    StrategyDecision, StrategyState, session_entry_time_rejection,
)
from kiwoom_monitor.application.mock_automation_execution import (
    assess_mock_unfilled_gate_bundle_from_risk,
    dispatch_mock_automation_decision_from_risk,
    dispatch_mock_unfilled_replacement_from_risk,
    unfilled_execution_policy_rejections,
    emergency_stop_mock_automation,
)
from kiwoom_monitor.application.mock_automation_session_clock import (
    SESSION_CLOCK_VERSION, clock_exit_decision, session_clock_assessment,
)
from kiwoom_monitor.application.order_lifecycle import (
    prepare_unfilled_management_preflight, validate_unfilled_management_preflight,
    UNFILLED_ATTEMPT_PLAN_VERSION, evaluate_unfilled_order_management_shadow,
    project_unfilled_native_attempts, seal_unfilled_attempt_plan, validate_unfilled_attempt_plan,
    protective_cancel_recovery,
    prepare_unfilled_reprice_evidence,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    record_mock_automation_recovery_from_risk,
)
from kiwoom_monitor.application.mock_automation_risk import mock_automation_protection_trigger
from kiwoom_monitor.application.research_families import get_research_family, parse_strategy_config
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
    replay_candidate_universe,
    replay_krx_minute_bars,
)
from kiwoom_monitor.domain.execution_activation import MockAutomationOperatingSpec
from kiwoom_monitor.domain.order_contract import OrderIntent, OrderSide, OrderState, OrderType, TERMINAL_ORDER_STATES
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


logger = logging.getLogger(__name__)
RUNNER_CHECKPOINT_VERSION = "mock_automation_runner_checkpoint/v1"
INPUT_KINDS = ("top20_membership", "minute_bar")
CLOCK_COLLECTION = "execution_mock_automation_clock_current"
PROTECTION_REVIEW_VERSION = "mock-protection-review/v1"


class MockAutomationRunner:
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
        poll_seconds: float = 1.0,
        price_source=None,
    ) -> None:
        context = bundle.automation_context
        if (
            context is None
            or bundle.account_ref != spec.account_scope.account_ref
            or bundle.run_id != context.execution_run_id
            or context.spec_id != spec.spec_id
        ):
            raise ValueError("MOCK_AUTOMATION_RUNNER_CONTEXT_MISMATCH")
        self._store = store
        self._bundle = bundle
        self._spec = spec
        self._repository = ForwardEvaluationRepository(store)
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
        self._family = get_research_family(family_id)
        self._config = parse_strategy_config(family_id, candidate.get("parameters", {}))
        if spec.session_profile != str(candidate.get("session_profile", {}).get("profile", "")):
            raise ValueError("MOCK_AUTOMATION_SESSION_PROFILE_MISMATCH")
        self._poll_seconds = max(0.25, float(poll_seconds))
        self._price_source = price_source
        self._unfilled_cost_model = None
        if spec.unfilled_execution_policy is not None:
            from kiwoom_monitor.application.research_execution import SimulationCostModel
            execution = candidate.get('execution_model')
            raw = execution.get('cost_model') if isinstance(execution,Mapping) else None
            if raw is not None:
                try:
                    self._unfilled_cost_model = SimulationCostModel(**raw)
                except (ValueError,TypeError):
                    pass  # Invalid/missing modeled cost never blocks protective management.
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
        self._cursor = 0
        self._fill_cursor = 0
        self._checkpoint_revision = 0
        self._checkpoint_pending = False
        self._last_checkpoint_content: dict[str, Any] | None = None
        self._state = StrategyState()
        self._position_state_lock = Lock()
        self._position_state_writers = self._position_state_revision = 0
        self._position_state_available_at = None
        self._position_state_failed = False
        self._bars: dict[tuple[str, str], KrxMinuteBarFrame] = {}
        self._universes: list[CandidateUniverseFrame] = []
        self._seen_fill_ids: set[str] = set()
        self._status: dict[str, Any] = {
            "state": "WARMUP", "reason": "not_started", "orders_enabled": False,
        }
        self._latency: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._bootstrap_pending = True
        self._delivery_protocol: str | None = None
        self._frame_recovery: ObservationFrameRecovery | None = None
        self._frame_recovery_receipt: dict[str, Any] | None = None
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
        self._wake_event: asyncio.Event | None = None
        self._wake_loop: asyncio.AbstractEventLoop | None = None
        self._native_risk_listener = None
        self._stop_loss_cut = None
        self._native_risk_delivery = None
        self._restore_or_bootstrap()
        if self._config.session_exit_policy:
            rows = self._store.load_documents(CLOCK_COLLECTION, self._bundle.account_ref, 1)
            clock = rows[0].get("document") if rows else None
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
                self._session_clock = dict(clock)
        monitor = getattr(self._bundle, "monitor", None)
        setter = getattr(monitor, "set_automation_risk_listener", None)
        if callable(setter):
            self._native_risk_listener = self._on_native_risk
            try:
                setter(self._native_risk_listener)
            except BaseException:
                monitor.clear_automation_risk_listener(self._native_risk_listener)
                self._native_risk_listener = None
                raise
        source_setter = getattr(monitor, 'set_automation_execution_inputs_enabled', None)
        if self._spec.unfilled_execution_policy is not None and callable(source_setter):
            try:
                source_setter(True)
            except BaseException:
                source_setter(False)
                if self._native_risk_listener is not None:
                    monitor.clear_automation_risk_listener(self._native_risk_listener)
                    self._native_risk_listener = None
                raise

    @property
    def status(self) -> dict[str, Any]:
        value = {
            "version": "mock_automation_runner_status/v1",
            "account_ref": self._bundle.account_ref,
            "spec_id": self._spec.spec_id,
            "execution_run_id": self._bundle.run_id,
            "input_cursor": self._cursor,
            "fill_cursor": self._fill_cursor,
            "strategy_state": self._state.status,
            **self._status,
            "latency": dict(self._latency) if self._latency is not None else None,
        }
        if self._frame_recovery is not None:
            value["input_recovery"] = self._frame_recovery.receipt()
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
                self._config, self._state, self._bundle.runtime.server_now(),
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
        if (self._closing or self._bootstrap_pending or self._frame_recovery is not None
                or self._bundle.runtime.observation_in_progress
                or not self._position_state_lock.acquire(blocking=False)):
            return None
        try:
            if self._position_state_writers or self._position_state_failed or self._position_state_available_at is None:return None
            revision=self._position_state_revision;state=self._state;cursor=self._fill_cursor
            available=self._position_state_available_at
        finally:self._position_state_lock.release()
        if not self._protection_lock.acquire(blocking=False):return None
        try:notice=deepcopy(self._protection_review)
        finally:self._protection_lock.release()
        runtime_revision=self._bundle.runtime.observation_revision
        try:
            clock=deepcopy(self._session_clock)
            risk=observation.risk_snapshot
            if notice is not None:self._validate_protection_review(notice)
            if (risk is None or observation.account.account_ref!=self._bundle.account_ref
                    or risk.execution_run_id!=self._bundle.run_id or risk.binding_revision!=self._spec.binding_revision
                    or risk.data_path!=self._spec.data_path or not observation.reconciliation_complete
                    or not observation.open_orders_complete or observation.reasons):return None
            body={'version':'native-position-runner-state/v1','source_kind':'owner_ram',
                'account_ref':self._bundle.account_ref,'execution_run_id':self._bundle.run_id,
                'binding_revision':self._spec.binding_revision,'data_path':self._spec.data_path,
                'account_input_ref':observation.input_ref,'risk_snapshot_ref':risk.snapshot_id,
                'account_observed_at':observation.observed_at.isoformat(),'state_available_at':available.isoformat(),
                'state_revision':revision,'runtime_observation_revision':runtime_revision,'fill_cursor':cursor,
                'risk_event_high_watermark':risk.execution_event_high_watermark,'family':self._family.family_id,
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
            if (self._closing or self._bootstrap_pending or self._frame_recovery is not None
                    or self._session_clock!=clock or self._bundle.runtime.observation_in_progress
                    or runtime_revision!=self._bundle.runtime.observation_revision
                    or not self._position_state_lock.acquire(blocking=False)):return None
            try:
                if self._position_state_writers or self._position_state_failed or revision!=self._position_state_revision:return None
            finally:self._position_state_lock.release()
            if not self._protection_lock.acquire(blocking=False):return None
            try:
                if self._protection_review!=notice:return None
            finally:self._protection_lock.release()
            return body
        except (ValueError,TypeError,KeyError,RuntimeError):return None

    def _replace_strategy_state(self,state):
        available=self._position_state_time()
        with self._position_state_lock:
            self._state=state
            self._position_state_revision+=1
            self._position_state_available_at=available

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
                    or trigger.get("binding_revision") != self._spec.binding_revision
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
                validate_mock_stop_loss_proof(trigger['stop_loss'], self._spec, stop_loss_bps=self._config.stop_loss_bps)

    def _on_native_risk(self, cut) -> None:
        if self._closing:
            return
        risk, account = cut["risk"], cut["account"]
        self._stop_loss_cut = (risk, dict(account.positions))
        if (risk.account_as_of != account.as_of
                or account.account_ref != self._bundle.account_ref):
            raise ValueError("mock_protection_native_recovery_mismatch")
        now = self._bundle.runtime.server_now()
        trigger = mock_automation_protection_trigger(self._spec, risk,
            execution_run_id=self._bundle.run_id, now=now, account_positions=account.positions)
        self._native_risk_delivery = {"snapshot_id": risk.snapshot_id,
            "reconciliation_revision": risk.reconciliation_revision,
            "delivered_at": now.isoformat(), "triggered": trigger is not None}
        if (self._protection_pending() and self._protection_exit_recovery_revision is not None
                and self._wake_loop is not None and self._wake_event is not None):
            self._wake_loop.call_soon_threadsafe(self._wake_event.set)
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
                if self._wake_loop is not None and self._wake_event is not None:
                    self._wake_loop.call_soon_threadsafe(self._wake_event.set)
        if (self._spec.unfilled_execution_policy is not None
                and trigger is None):
            previous_stop = (self._protection_snapshot() or {}).get('risk_trigger', {}).get('stop_loss')
            if previous_stop is not None:
                price = {'symbol':previous_stop['symbol'], 'price_won':previous_stop['price_won'],
                    'available_at':datetime.fromisoformat(previous_stop['available_at']),
                    'input_ref':previous_stop['input_ref'], 'durable':False, 'market_status':'TRADABLE'}
                self.observe_automation_price(previous_stop['symbol'], price)
            else:
                for symbol, _ in risk.owned_entry_prices:
                    price = self._price_source(symbol) if self._price_source is not None else None
                    if price is not None:
                        self.observe_automation_price(symbol, price)
        if trigger is None:
            return  # Healthy recovery cannot erase an armed protection request.
        previous = self._protection_snapshot()
        stop = (previous or {}).get('risk_trigger', {}).get('stop_loss')
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

    def observe_automation_price(self, symbol, price):
        """RAM-only immediate stop cue; transport remains in the existing Runner."""
        if self._closing or self._spec.unfilled_execution_policy is None or self._stop_loss_cut is None:
            return
        risk, positions = self._stop_loss_cut
        if symbol not in dict(risk.owned_entry_prices):
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
                    'input_ref':proof['input_ref'], 'durable':False, 'market_status':'TRADABLE'}
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_stop_loss_trigger
        now = self._bundle.runtime.server_now()
        trigger = mock_automation_stop_loss_trigger(self._spec, risk, execution_run_id=self._bundle.run_id,
            now=now, price={**price, 'symbol':symbol}, account_positions=positions,
            stop_loss_bps=self._config.stop_loss_bps)
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
        now = self._bundle.runtime.server_now()
        if requested_at.tzinfo is None or requested_at > now:
            raise ValueError("invalid_mock_protection_review_time")
        with self._protection_lock:
            if self._closing:
                raise RuntimeError("MOCK_AUTOMATION_RUNNER_CLOSED")
            previous = self._protection_review
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
            self._validate_protection_review(notice)
            self._protection_review = notice
            loop, event = self._wake_loop, self._wake_event
        if loop is not None and event is not None:
            loop.call_soon_threadsafe(event.set)
        return deepcopy(notice)

    async def _pause_for_protection_review(self) -> bool:
        if not self._protection_pending():
            return False
        await self._poll_first_protection_exit()
        self._status = {**self._status, "state": "PROTECTION_REVIEW_REQUIRED", "reason": "protection_review_pending",
                        "orders_enabled": False}
        await drain_thread_call(self._save_checkpoint)
        return True

    async def _poll_first_protection_exit(self):
        if self._spec.unfilled_execution_policy is None:
            return
        monitor=getattr(self._bundle,'monitor',None)
        if not getattr(monitor,'_automation_execution_inputs_enabled',False):
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
                    and self._state_matches_risk(cut['risk'])):
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
                candidate=self._price_source(self._state.symbol) if self._price_source and self._state.status=='open' else None
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
                trigger=mock_automation_protection_trigger(self._spec,risk,execution_run_id=self._bundle.run_id,now=now)
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
                        or not self._state_matches_risk(risk) or self._state.status!='open'
                        or observation.account.positions.get(self._state.symbol)!=self._state.position_quantity
                        or trigger is not None and trigger['disposition']!='EXIT_REQUIRED'
                        or any(not 0 <= (now-at).total_seconds() <= age for at in (
                            risk.observed_at,risk.account_as_of,observation.observed_at,price['available_at']))):
                    raise ValueError('protection_exit_native_cut_or_control_changed')
                return control
            self._apply_new_fills()
            control=verify()
            if 'stop_loss' in notice.get('risk_trigger',{}):
                from kiwoom_monitor.application.mock_automation_risk import validate_mock_stop_loss_proof
                validate_mock_stop_loss_proof(notice['risk_trigger']['stop_loss'], self._spec, risk,
                    stop_loss_bps=self._config.stop_loss_bps)
            active,complete=self._bundle.repository.account_active_intents(self._bundle.account_ref)
            if active or not complete:
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
                    control_revision=control.control_revision,notice=notice,risk=risk,state=self._state,price=price)
            decision=validate_mock_protection_exit(self._protection_exit,self._spec,self._bundle.run_id)
            snapshot=self._protection_exit['snapshot']
            if (snapshot['control_revision']!=control.control_revision or snapshot['request_revision']!=notice['revision']
                    or snapshot['request_input_ref']!=notice['input_ref'] or snapshot['request_trigger']!=notice['risk_trigger']
                    or decision.quantity!=self._state.position_quantity
                    or self._bundle.runtime.server_now() >= datetime.fromisoformat(decision.decided_at)+timedelta(seconds=self._spec.maximum_data_gap_seconds)):
                raise ValueError('protection_exit_fixed_decision_changed_or_expired')
            self._status.update(first_protection_exit_state='PREPARED',first_protection_exit_reason='native_loss_protection')
            self._save_checkpoint()  # ACK the fixed decision before native QUEUED.
            verify()
            record_mock_automation_recovery_from_risk(self._repository,self._bundle.runtime,recovery,risk,
                account_ref=self._bundle.account_ref,spec_id=self._spec.spec_id)
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
        decision=validate_mock_protection_exit(self._protection_exit,self._spec,self._bundle.run_id)
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

    def resolve_protection_review(self, *, expected_revision: int, recovery, risk) -> dict[str, Any]:
        """Explicit OFF review completion, only with current fully flat native proof."""
        if self._closing or self._task is not None or not self._unfilled_preflight_lock.acquire(blocking=False):
            raise RuntimeError("mock_protection_resolution_requires_idle_runner")
        try:
            notice = self._protection_snapshot()
            if notice is None or notice["state"] != "PENDING" or notice["revision"] != expected_revision:
                raise ValueError("mock_protection_review_revision_changed")
            with self._bundle.runtime.automation_decision_guard():
                control = self._repository.load_mock_automation_control(self._bundle.account_ref)
                current = self._repository.load_latest_mock_automation_risk(self._bundle.account_ref)
                admission = self._repository.load_mock_automation_admission_for_spec(self._bundle.account_ref, self._spec.spec_id)
                linked = self._repository.load_latest_mock_automation_recovery(self._bundle.account_ref, admission.admission_id) if admission else None
                active = self._bundle.repository.account_active_intents(self._bundle.account_ref)
                now = self._bundle.runtime.server_now()
                if (current != risk or control is None or control.active_spec_id != self._spec.spec_id
                        or control.execution_run_id != self._bundle.run_id or control.desired_state.value != "RUNNING"
                        or linked is None or linked.risk_snapshot_id != risk.snapshot_id
                        or linked.risk_reconciliation_revision != risk.reconciliation_revision
                        or risk.account_ref != self._bundle.account_ref or risk.execution_run_id != self._bundle.run_id
                        or risk.account_as_of != recovery.account.as_of or not risk.reconciliation_complete
                        or recovery.account.account_ref != self._bundle.account_ref or recovery.account.positions
                        or any(order.state not in TERMINAL_ORDER_STATES for order in recovery.orders)
                        or risk.owned_position_quantities or risk.pending_sell_quantities or risk.submission_unknown_count
                        or risk.balance_mismatch_count or not active[1] or active[0]
                        or self._spec.maximum_data_gap_seconds is None
                        or risk.broker_query_started_at < datetime.fromisoformat(notice["requested_at"])
                        or not 0 <= (now - risk.observed_at).total_seconds() <= self._spec.maximum_data_gap_seconds
                        or not 0 <= (now - risk.account_as_of).total_seconds() <= self._spec.maximum_data_gap_seconds):
                    raise ValueError("mock_protection_resolution_native_flat_unverified")
                self._apply_new_fills()
                if not self._state_matches_risk(risk):
                    raise ValueError("mock_protection_resolution_native_flat_unverified")
                # Keep the request visible throughout native I/O. A newer cue
                # wins; resolution cannot overwrite it while saving its ACK.
                with self._protection_lock:
                    if self._protection_review != notice:
                        raise ValueError("mock_protection_review_revision_changed")
                    resolved = {**notice, "state": "RESOLVED_FLAT", "resolved_risk_snapshot_id": risk.snapshot_id,
                                "resolved_risk_revision": risk.reconciliation_revision}
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
            if self._closing or self._task is not None or self._bootstrap_pending or self._frame_recovery is not None:
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
        with self._bundle.runtime.automation_decision_guard():
            control = self._repository.load_mock_automation_control(self._bundle.account_ref)
            if (control is None or control.desired_state.value != 'RUNNING'
                    or control.active_spec_id != self._spec.spec_id
                    or control.execution_run_id != self._bundle.run_id
                    or control.control_revision != anchor['scope']['control_revision']):
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
            root = self._bundle.repository.load(policy.anchor_intent_id)
            if root is None:
                raise ValueError('unfilled_native_root_missing')
            self._apply_new_fills()  # Existing detailed-fill reader; no synthetic position or new writer.
            if not self._state_matches_risk(risk) or self._fill_cursor > risk.execution_event_high_watermark:
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
            if marker is not None and self._bundle.repository.load(marker) is not None:
                raise ValueError('unfilled_v1_preview_marker_in_native_ledger')
            next_child_id = 'unfilled-native-child:' + hashlib.sha256(
                f'{root.intent.intent_id}:{len(plan["children"]) + 1}'.encode()).hexdigest()
            if self._bundle.repository.load(next_child_id) is not None:
                raise ValueError('unfilled_native_child_without_preserved_manifest')
            records = [self._bundle.repository.load(slot['intent']['intent_id']) for slot in plan['children']]
            events = [self._bundle.repository.events(slot['intent']['intent_id']) for slot in plan['children']]
            root_events = self._bundle.repository.events(root.intent.intent_id)
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
                'state_before': self._state.to_dict(), 'state_after': self._state.to_dict(),
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
            if (self._closing or self._protection_snapshot() != protection_notice
                    or self._repository.load_mock_automation_control(self._bundle.account_ref) != control
                    or self._repository.load_latest_mock_automation_risk(self._bundle.account_ref) != risk
                    or self._bundle.repository.load(root.intent.intent_id) != root
                    or self._bundle.repository.events(root.intent.intent_id) != root_events
                    or any(self._bundle.repository.load(slot['intent']['intent_id']) != record
                           for slot, record in zip(plan['children'], records + [None] * (len(plan['children']) - len(records))))
                    or any(self._bundle.repository.events(slot['intent']['intent_id']) != history
                           for slot, history in zip(plan['children'], events + [()] * (len(plan['children']) - len(events))))):
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
        if self._closing or self._task is not None:
            raise RuntimeError('unfilled_execution_requires_idle_acknowledged_runner')
        if not self._unfilled_preflight_lock.acquire(blocking=False):
            raise RuntimeError('unfilled_preflight_already_in_progress')
        try:
            if self._closing or self._task is not None:
                raise RuntimeError('unfilled_execution_requires_idle_acknowledged_runner')
            return self._execute_unfilled_replacement_locked(**inputs)
        finally:
            self._unfilled_preflight_lock.release()

    def _execute_unfilled_replacement_locked(self, **inputs):
        if self._closing or self._checkpoint_pending:
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
            if self._closing or self._task is not None or self._bootstrap_pending or self._frame_recovery is not None:
                raise RuntimeError("unfilled_preflight_requires_idle_ready_runner")
            return self._preview_unfilled_management_locked(**inputs)
        finally:
            self._unfilled_preflight_lock.release()

    def _preview_unfilled_management_locked(self, *, policy, broker_snapshot, target, observation, assessment):
        control = self._repository.load_mock_automation_control(self._bundle.account_ref)
        if (control is None or control.desired_state.value != "RUNNING"
                or control.active_spec_id != self._spec.spec_id
                or control.execution_run_id != self._bundle.run_id):
            raise ValueError("unfilled_preflight_control_not_running")
        record = self._bundle.repository.load(policy.anchor_intent_id)
        if record is None:
            raise ValueError("unfilled_preflight_native_parent_missing")
        now = self._bundle.runtime.server_now()
        if not 0 <= (now - target.decision_time).total_seconds() <= policy.max_snapshot_age_seconds:
            raise ValueError("unfilled_preflight_cut_stale_or_future")
        scope = {"account_ref": self._bundle.account_ref, "execution_run_id": self._bundle.run_id,
                 "spec_id": self._spec.spec_id, "candidate_package_hash": self._spec.candidate_package_hash,
                 "control_revision": control.control_revision}
        proposed = prepare_unfilled_management_preflight(
            scope=scope, policy=policy, record=record, broker_snapshot=broker_snapshot,
            target=target, observation=observation, assessment=assessment,
            previous=self._unfilled_preflight,
        )
        child_id = proposed["reserved_child_intent_id"]
        if child_id is not None and self._bundle.repository.load(child_id) is not None:
            raise ValueError("unfilled_preflight_native_child_requires_reconciliation")
        self._unfilled_preflight = proposed
        self._save_checkpoint()
        if (self._closing
                or self._bundle.repository.load(record.intent.intent_id) != record
                or self._repository.load_mock_automation_control(self._bundle.account_ref) != control
                or (child_id is not None and self._bundle.repository.load(child_id) is not None)):
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
        from kiwoom_monitor.application.mock_automation_session_clock import content_id
        from kiwoom_monitor.application.order_lifecycle import UnfilledOrderManagementPolicy, unfilled_plan_intent
        if (not isinstance(body, Mapping) or self._spec.unfilled_execution_policy is None
                or set(body) != {'version','spec_id','account_ref','execution_run_id','control_revision',
                                  'operating_policy_id','root_intent','purpose','target_quantity','policy','registration_id'}
                or body['version'] != 'mock-unfilled-registration/v1'
                or (body['spec_id'], body['account_ref'], body['execution_run_id'], body['operating_policy_id']) !=
                    (self._spec.spec_id, self._bundle.account_ref, self._bundle.run_id, self._spec.unfilled_execution_policy.policy_id)
                or type(body['control_revision']) is not int or body['control_revision'] <= 0
                or type(body['target_quantity']) is not int or body['target_quantity'] < 0
                or body['registration_id'] != content_id('unfilled_registration', {k:v for k,v in body.items() if k != 'registration_id'})):
            raise ValueError('invalid_unfilled_native_registration')
        intent = unfilled_plan_intent(body['root_intent'])
        raw = dict(body['policy'])
        for name in ('anchor_available_at','valid_until','wait_until','protection_deadline'):
            raw[name] = datetime.fromisoformat(raw[name])
        policy = UnfilledOrderManagementPolicy(**raw)
        if (intent.account_ref != self._bundle.account_ref or intent.run_id != self._bundle.run_id
                or intent.intent_id.startswith('unfilled-native-child:') or policy.anchor_intent_id != intent.intent_id
                or policy.anchor_price_won != intent.limit_price or policy.purpose != body['purpose']
                or (intent.side is OrderSide.BUY) != (policy.purpose == 'BUY_OPPORTUNITY')):
            raise ValueError('unfilled_native_registration_root_mismatch')
        frozen = self._spec.unfilled_execution_policy
        end = intent.created_at + timedelta(seconds=frozen.maximum_management_seconds)
        if (body['target_quantity'] != (intent.quantity if intent.side is OrderSide.BUY else 0)
                or policy.anchor_available_at != intent.created_at
                or policy.anchor_price_input_ref != 'original-decision:' + intent.decision_id
                or policy.valid_until != end or policy.wait_until != min(end, intent.expires_at)
                or policy.protection_deadline != min(end, intent.created_at + timedelta(seconds=frozen.max_snapshot_age_seconds))
                or any(getattr(policy, name) != getattr(frozen, name) for name in (
                    'maximum_reprices','maximum_adverse_slippage_bps','protective_slippage_bps',
                    'max_snapshot_age_seconds','minimum_net_advantage_won','uncertainty_multiplier',
                    'minimum_paired_samples','reprice_horizon_seconds'))):
            raise ValueError('unfilled_native_registration_frozen_policy_mismatch')
        return policy

    def _dispatch_original_and_register(self, decision, recovery, risk, *, protection_exit_id=None):
        result = dispatch_mock_automation_decision_from_risk(self._repository, self._bundle.runtime, decision,
            recovery, risk, account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id,
            strategy_ref=self._spec.strategy_ref,
            **({'protection_exit_id':protection_exit_id} if protection_exit_id is not None else {}))
        if result[1] is not None:
            self._register_native_unfilled(result[1], result[0], decision)
        return result

    async def _produce_native_unfilled_inputs(self, control):
        if self._spec.unfilled_execution_policy is None:
            return
        monitor = getattr(self._bundle, 'monitor', None)
        if not getattr(monitor, '_automation_execution_inputs_enabled', False):
            return
        with self._unfilled_input_lock:
            if self._unfilled_input is not None:
                return  # Explicit supplied cuts keep their existing isolated seam.
        if self._unfilled_registration is None and self._unfilled_preflight is not None:
            return
        if self._unfilled_registration is None and self._unfilled_discovery_pending:
            self._unfilled_discovery_pending = False
            rows = await drain_thread_call(self._bundle.runtime.load_active_intents)
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
        if cut is None:
            self._unfilled_source_status = {'state':'BLOCKED','reason':'native_account_cut_unavailable'}
            return  # Wait for owner's ACK callback, not a repeated broker request.
        if registration['control_revision'] != control.control_revision:
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
        _,judgment_at=self._current_unfilled_judgment(policy,now)
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
        assessment = UnfilledOrderAssessment(True if record.intent.side is OrderSide.BUY else None, None,
            not protecting, protecting, 'original-decision:' + registration['root_intent']['decision_id'],
            policy.anchor_available_at, thesis_until, 'native-risk:' + risk.snapshot_id, risk.observed_at,
            risk.observed_at + timedelta(seconds=policy.max_snapshot_age_seconds),
            'TRADABLE' if available else 'UNKNOWN', price['price_won'] if available else None,
            price['input_ref'] if available else '', price['available_at'] if available else None, 0, None)
        paired = {'ev_status':'EV_UNAVAILABLE','reason':'unfilled_paired_population_not_selected'}
        if not protecting:
            judgment, stamp = self._current_unfilled_judgment(policy,now)
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
        await drain_thread_call(record_mock_automation_recovery_from_risk,
            self._repository, self._bundle.runtime, recovery, risk,
            account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id)
        if generation != self._unfilled_source_generation:
            self._unfilled_source_pending = True
            self._unfilled_source_status = {'state':'BLOCKED','reason':'native_account_cut_superseded'}
            return  # A newer ACK callback must not be erased by this older cut.
        self.offer_unfilled_execution_inputs(policy=policy, broker_snapshot=broker, target=target,
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
        if self._wake_loop is not None and self._wake_event is not None:
            self._wake_loop.call_soon_threadsafe(self._wake_event.set)
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
        # The first accepted BUY has no exact fill history, so native Risk keeps
        # PnL/recovery unverified. A full, mapped, flat account may cancel its
        # known unfilled BUY; this never admits a replacement or clears Risk.
        return (policy.purpose == 'BUY_OPPORTUNITY' and not risk.reconciliation_complete
                and observation.open_orders_complete and observation.reconciliation_complete
                and not observation.unmapped_broker_order_ids
                and not risk.submission_unknown_count and not risk.balance_mismatch_count
                and not risk.owned_position_quantities and not risk.pending_sell_quantities
                and not any(observation.account.positions.values())
                and bool(observation.open_orders)
                and all(row.intent.side is OrderSide.BUY and row.state is OrderState.ACCEPTED
                        and row.filled_quantity == row.detailed_filled_quantity == row.broker_reported_filled_quantity == 0
                        for row in observation.open_orders))

    def offer_unfilled_execution_inputs(self, **inputs) -> bool:
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
        encoded_values = {key: asdict(value) for key, value in snapshot.items()}
        encoded_values['risk'] = risk.to_dict()
        # The observation carries the same typed Risk, including trading_date.
        encoded_values['observation']['risk_snapshot'] = risk.to_dict()
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
            self._unfilled_input = (identity[2], snapshot, cause)
            self._unfilled_delivery = {'input_id': identity[2], 'decision_time': identity[0].isoformat(),
                                      'risk_revision': identity[1], 'state': 'PENDING', 'order_authorized': False}
            should_wake = cause != self._unfilled_processed_cause or self._unfilled_due() is not None
            loop, event = self._wake_loop, self._wake_event
        if should_wake and loop is not None and event is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass  # Close retired the loop; the cut can no longer authorize work.
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
        if self._checkpoint_pending:
            await drain_thread_call(self._save_checkpoint)
        with self._unfilled_input_lock:
            pending, self._unfilled_input = self._unfilled_input, None
        if pending is None:
            return False
        identity, inputs, cause = pending
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
            result = await self._manage_unfilled_inputs(inputs)
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

    async def _manage_unfilled_inputs(self, inputs):
        if self._cancel_recovery is not None and inputs['policy'].purpose == 'PROTECTIVE_EXIT':
            record = await drain_thread_call(self._bundle.runtime.load_intent,self._cancel_recovery['managed_intent_id'])
            events = await drain_thread_call(self._cancel_lineage_events)
            self._set_cancel_recovery(protective_cancel_recovery(record,events,inputs['policy'],
                self._spec.unfilled_execution_policy,self._bundle.runtime.server_now()),events,inputs['policy'])
        plan = await drain_thread_call(self._assess_unfilled_action_locked, inputs)
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

    async def _cancel_unfilled(self, inputs, plan):
        gateway = getattr(self._bundle, 'gateway', None)
        if gateway is None:
            raise ValueError('unfilled_automation_cancel_gateway_unavailable')
        root = await drain_thread_call(self._bundle.runtime.load_intent, inputs['policy'].anchor_intent_id)
        reasons = unfilled_execution_policy_rejections(self._spec, inputs['policy'], root)
        if (plan['result']['after_cancel'] == 'REPRICE' and plan['result']['purpose'] != 'PROTECTIVE_EXIT'
                and (not self._spec.unfilled_execution_policy.allow_supplied_paired_mock_evidence
                     or plan['result']['ev_status'] != 'SUPPLIED_PAIRED_UNFILLED_ESTIMATE')):
            reasons += ('UNFILLED_PAIRED_EVIDENCE_NOT_ADMITTED_FOR_MOCK',)
        if reasons:
            raise ValueError(';'.join(reasons))
        expected = await drain_thread_call(self._bundle.runtime.load_intent, plan['result']['intent_ref'])
        proof = {}
        retry_claim = None
        if inputs['policy'].purpose == 'PROTECTIVE_EXIT':
            events = await drain_thread_call(self._cancel_lineage_events)
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
            control_revision=plan['anchor']['scope']['control_revision'],
            before_cancel_claim=revalidate,
            **({'cancel_retry_claim':retry_claim} if retry_claim is not None else {}))
        unresolved = record.state not in TERMINAL_ORDER_STATES
        rejected = unresolved and record.state is not OrderState.CANCEL_PENDING
        state = 'CANCEL_UNCONFIRMED' if rejected else 'CANCEL_REQUESTED' if unresolved else 'CANCEL_RESOLVED'
        if rejected and plan['result']['risk_path_required'] and not self._protection_pending():
            self.request_protection_review(reason='unfilled_protective_cancel_unconfirmed',
                input_ref='native-cancel:' + record.intent.intent_id, requested_at=self._bundle.runtime.server_now())
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
        return state, None

    def _cancel_lineage_events(self):
        plan = self._unfilled_attempt_plan
        ids = (plan['anchor']['root_intent']['intent_id'],*(child['intent']['intent_id'] for child in plan['children']))
        return tuple(row for intent_id in ids for row in self._bundle.repository.events(intent_id))

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
        if (risk != self._repository.load_latest_mock_automation_risk(self._bundle.account_ref)
                or risk.account_ref != self._bundle.account_ref or risk.execution_run_id != self._bundle.run_id
                or (not risk.reconciliation_complete and not flat_buy_cancel) or not self._state_matches_risk(risk)
                or any(not 0 <= (now - stamp).total_seconds() <= age
                       for stamp in (risk.observed_at, observation.observed_at, observation.account.as_of))):
            raise ValueError('unfilled_cancel_native_risk_or_owned_state_changed')
        trigger = mock_automation_protection_trigger(self._spec, risk, execution_run_id=self._bundle.run_id,
            now=now, account_positions=observation.account.positions)
        if (trigger is not None and trigger['disposition'] == 'RECONCILE_REQUIRED'
                and inputs['policy'].purpose != 'BUY_OPPORTUNITY'):
            raise ValueError('unfilled_cancel_native_reconciliation_required')
        if (plan['result']['after_cancel'] == 'REPRICE' and plan['result']['purpose'] != 'PROTECTIVE_EXIT'
                and (not risk.cost_complete or risk.daily_net_pnl_won is None)):
            raise ValueError('unfilled_cancel_current_cost_evidence_unavailable')
        current = self._bundle.runtime.load_intent(expected.intent.intent_id)
        # The runtime has compared every business field to expected already.
        # Its newer last_broker_as_of is a real persisted REST confirmation;
        # preserve the original exact fill IDs/quantities, never invent fills.
        if current.last_broker_as_of is None or current.last_broker_as_of > observation.account.as_of:
            raise ValueError('unfilled_cancel_broker_confirmation_missing')
        broker = replace(inputs['broker_snapshot'], as_of=current.last_broker_as_of)
        target = replace(inputs['target'], decision_time=now, account_input_ref=observation.input_ref,
                         orders_input_ref=observation.input_ref)
        assessment = replace(inputs['assessment'], risk_input_ref='native-risk:' + risk.snapshot_id,
            risk_available_at=risk.observed_at,
            risk_valid_until=min(inputs['policy'].valid_until, risk.observed_at + timedelta(seconds=age)),
            protection_required=inputs['assessment'].protection_required or self._protection_pending() or trigger is not None)
        root = self._bundle.runtime.load_intent(inputs['policy'].anchor_intent_id)
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

    async def start(self) -> None:
        if self._closing:
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_CLOSED")
        if self._unfilled_preflight_lock.locked():
            raise RuntimeError("unfilled_preflight_already_in_progress")
        if self._task is None or self._task.done():
            self._wake_loop = asyncio.get_running_loop()
            self._wake_event = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="mock-automation-runner")

    async def close(self) -> None:
        with self._protection_lock:
            self._closing = True
            self._wake_loop = None
        with self._unfilled_input_lock:
            self._unfilled_input = None
        source_setter = getattr(getattr(self._bundle, 'monitor', None), 'set_automation_execution_inputs_enabled', None)
        if self._spec.unfilled_execution_policy is not None and callable(source_setter):
            source_setter(False)
        if self._native_risk_listener is not None:
            self._bundle.monitor.clear_automation_risk_listener(self._native_risk_listener)
            self._native_risk_listener = None
        if self._task is None:
            def join_native_execution():
                with self._unfilled_preflight_lock:
                    pass
            await drain_thread_call(join_native_execution)
            if self._protection_snapshot() is not None:
                await drain_thread_call(self._save_checkpoint, force=True)
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        await drain_thread_call(self._save_checkpoint, force=True)

    async def _run(self) -> None:
        if self._wake_event is None:
            self._wake_loop = asyncio.get_running_loop()
            self._wake_event = asyncio.Event()
        while True:
            self._wake_event.clear()
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("automatic mock runner iteration failed")
                self._status = {
                    **(self._status if self._frame_recovery is not None else {}),
                    "state": "ERROR", "reason": type(error).__name__,
                    "detail": str(error), "orders_enabled": False,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    await drain_thread_call(self._save_checkpoint)
                except Exception:
                    logger.exception("automatic mock runner checkpoint failed")
            try:
                timeout = self._poll_seconds
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
                await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

    async def run_once(self, *, limit: int = 1000) -> int:
        if not self._unfilled_preflight_lock.acquire(blocking=False):
            raise RuntimeError("unfilled_preflight_already_in_progress")
        try:
            poll_started = perf_counter()
            # FILL reads mutate checkpoint cursor/state under every profile.
            # Close must join them before taking its final checkpoint snapshot.
            await drain_thread_call(self._apply_new_fills)
            native_call = (drain_thread_call if self._frame_recovery is not None
                           or self._config.session_exit_policy else asyncio.to_thread)
            control = await native_call(
                self._repository.load_mock_automation_control, self._bundle.account_ref,
            )
            if (control is None or control.active_spec_id != self._spec.spec_id
                    or getattr(control, "execution_run_id", self._bundle.run_id) != self._bundle.run_id):
                status = {
                    **(self._status if self._frame_recovery is not None else {}),
                    "state": "BLOCKED", "reason": "automation_control_missing_or_replaced",
                    "orders_enabled": False,
                }
                if status != self._status or self._checkpoint_pending:
                    self._status = status
                    await drain_thread_call(self._save_checkpoint)
                return 0
            if control.desired_state.value != "RUNNING":
                if (self._config.session_exit_policy and self._session_clock is not None
                        and control.reason.startswith("session_clock:")):
                    await self._clock_call(control)
                    if self._checkpoint_pending:
                        await drain_thread_call(self._save_checkpoint)
                    return 0
                status = {
                    **(self._status if self._frame_recovery is not None else {}),
                    "state": "STOPPED", "reason": "user_stopped", "orders_enabled": False,
                }
                if status != self._status or self._checkpoint_pending:
                    self._status = status
                    await drain_thread_call(self._save_checkpoint)
                return 0

            unfilled_ready = ((not self._bootstrap_pending and self._frame_recovery is None)
                    or self._unfilled_registration is not None
                       and (self._protection_pending() or self._unfilled_registration['purpose'] == 'PROTECTIVE_EXIT'))
            if unfilled_ready:
                await self._produce_native_unfilled_inputs(control)
            if unfilled_ready and await self._unfilled_call():
                await self._pause_for_protection_review()
                return 0
            if await self._pause_for_protection_review():
                return 0
            load_started = perf_counter()
            if self._bootstrap_pending:
                if await self._clock_call(control):
                    return 0
                await drain_thread_call(self._bootstrap)
                return 0  # Never evaluate historical seed or dispatch its orders.
            if self._frame_recovery is not None:
                if await self._clock_call(control):
                    return 0
                await drain_thread_call(self._recover_frames, limit)
                return 0
            if self._checkpoint_pending:
                # Persist the consumed prefix before loading any subsequent input.
                # Fills and current control were refreshed above; never re-evaluate it.
                await drain_thread_call(self._save_checkpoint)
            observations = await asyncio.to_thread(
                self._store.load_observation_revisions_after,
                self._cursor, INPUT_KINDS, limit,
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
                self._cursor = max(self._cursor, int(observation.get("accepted_sequence", 0)))
                consumed += 1
                await drain_thread_call(self._save_checkpoint)
                max_processing_ms = max(
                    max_processing_ms, round((perf_counter() - observation_started) * 1000),
                )
                if observation.get("kind") == "minute_bar":
                    bar_count += 1
                    processed_at = datetime.now(timezone.utc)
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
                "last_poll_completed_at": datetime.now(timezone.utc).isoformat(),
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
            "owner": self._bundle.account_ref, "key": "current",
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
        assessment = session_clock_assessment(
            self._config, self._state, now, active, management_preflight=self._unfilled_attempt_plan or self._unfilled_preflight,
        )
        stopped = control.desired_state.value != "RUNNING"
        if not stopped and self._session_clock is not None and (
            self._session_clock["control_revision"] != control.control_revision
            or (self._session_clock.get("phase") == "COMPLETED" and assessment["reasons"])
            or (self._session_clock.get("decision") is not None and self._state.status == "open"
                and self._session_clock.get("position_opened_at") != self._state.opened_at)
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
                "position_opened_at": self._state.opened_at,
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
            recovery = await self._bundle.monitor.refresh_recovery()
            await drain_thread_call(self._apply_new_fills)
            active = await drain_thread_call(
                self._bundle.repository.active_intents,
                "mock", self._bundle.account_ref, self._bundle.run_id,
            )
            risk = await drain_thread_call(
                self._repository.load_latest_mock_automation_risk, self._bundle.account_ref,
            )
            if risk is None or not risk.reconciliation_complete or not self._state_matches_risk(risk):
                raise ValueError("clock_broker_reconciliation_unverified")
            if active:
                await self._clock_status("WAITING_ORDER", "clock_exit_waits_for_broker_order")
                await self._cancel_clock_orders(all_orders=False)
                return True
            refreshed = session_clock_assessment(
                self._config, self._state, risk.observed_at, active,
            )
            if not refreshed["exit_required"]:
                if self._state.status == "open":
                    raise ValueError("clock_recovery_predates_exit_due")
                await self._clock_status("COMPLETED", "actual_exit_fill_confirmed")
                return True
            frames = tuple(self._bars.values()) + tuple(replay_krx_minute_bars(
                observations, strict=True, session_profile=str(self._spec.session_profile),
            ))
            bars = [bar for bar in frames if bar.code == self._state.symbol
                    and datetime.fromisoformat(bar.available_at) <= risk.observed_at]
            bar = max(bars, key=lambda value: (
                datetime.fromisoformat(value.bar_end), datetime.fromisoformat(value.available_at), value.revision_id),
                      default=None)
            if self._bootstrap_pending or self._frame_recovery is not None:
                raise ValueError("clock_price_frames_not_ready")
            decision, snapshot = clock_exit_decision(
                spec_id=self._spec.spec_id, run_id=self._bundle.run_id,
                state=self._state, assessment=refreshed, bar=bar, now=risk.observed_at,
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
            await drain_thread_call(
                record_mock_automation_recovery_from_risk,
                self._repository, self._bundle.runtime, recovery, risk,
                account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id,
            )
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
            recovery = await self._bundle.monitor.refresh_recovery()
            await drain_thread_call(self._apply_new_fills)
            now = self._bundle.runtime.server_now()
            risk = await drain_thread_call(
                self._repository.load_latest_mock_automation_risk, self._bundle.account_ref,
            )
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
                if (latest is None or latest.active_spec_id != self._spec.spec_id
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
        if observation.get("kind") == "top20_membership":
            frames = replay_candidate_universe((observation,))
            if frames:
                self._universes.append(frames[0])
                self._trim_universes(frames[0].available_at)
            return
        frames = replay_krx_minute_bars(
            (observation,), strict=True, session_profile=str(self._spec.session_profile),
        )
        if not frames:
            return
        evaluation_bar = frames[0]
        self._bars[(evaluation_bar.code, evaluation_bar.observation_key)] = evaluation_bar
        self._trim_bars(evaluation_bar.code)
        await drain_thread_call(self._apply_new_fills)
        active = await asyncio.to_thread(
            self._bundle.repository.active_intents,
            "mock", self._bundle.account_ref, self._bundle.run_id,
        )
        if active:
            registration=self._unfilled_registration
            if (registration is not None and registration['purpose']=='TAKE_PROFIT'
                    and self._state.status=='open' and self._state.symbol==evaluation_bar.code
                    and not self._protection_pending()):
                # Same registered pure family, once for this actual bar. Its
                # result grants no orders and never changes fill-owned state.
                evaluation=self._family.evaluate_bar(run_id=self._bundle.run_id,evaluation_bar=evaluation_bar,
                    bar_history=tuple(self._bars.values()),universe_frames=tuple(self._universes),
                    config=self._config,state=self._state)
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
        evaluation = self._family.evaluate_bar(
            run_id=self._bundle.run_id,
            evaluation_bar=evaluation_bar,
            bar_history=tuple(self._bars.values()),
            universe_frames=tuple(self._universes),
            config=self._config,
            state=self._state,
        )
        self._replace_strategy_state(evaluation.state)
        decision = evaluation.decision
        if decision.final_action not in {"ENTER", "EXIT"}:
            self._status = {
                "state": "RUNNING", "reason": ";".join(decision.reasons),
                "last_decision_id": decision.decision_id, "orders_enabled": False,
                "updated_at": decision.decided_at,
            }
            return

        recovery = await self._bundle.monitor.refresh_recovery()
        risk = await asyncio.to_thread(
            self._repository.load_latest_mock_automation_risk, self._bundle.account_ref,
        )
        if risk is None:
            raise RuntimeError("MOCK_AUTOMATION_RISK_MISSING")
        if not self._state_matches_risk(risk):
            self._status = {
                "state": "BLOCKED", "reason": "strategy_state_risk_mismatch",
                "last_decision_id": decision.decision_id, "orders_enabled": False,
                "updated_at": decision.decided_at,
            }
            return
        await asyncio.to_thread(
            record_mock_automation_recovery_from_risk,
            self._repository, self._bundle.runtime, recovery, risk,
            account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id,
        )
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
        gate, record, _ = await drain_thread_call(self._dispatch_original_and_register, decision, recovery, risk)
        self._status = {
            "state": "ORDER_SUBMITTED" if record is not None else "BLOCKED",
            "reason": ";".join(gate.reasons) if gate.reasons else "approved",
            "last_decision_id": decision.decision_id,
            "last_gate_id": gate.gate_id,
            "pending_intent_id": record.intent.intent_id if record is not None else "",
            "orders_enabled": False,
            "updated_at": decision.decided_at,
        }

    def _apply_new_fills(self) -> None:
        # Fence only publication of copied facts. Never hold this lock across I/O.
        with self._position_state_lock:
            if self._position_state_writers:self._position_state_failed=True
            self._position_state_writers+=1
            before=(self._state,self._fill_cursor)
        try:
            self._apply_new_fills_core()
            available=self._position_state_time()
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

    def _apply_new_fills_core(self) -> None:
        while True:
            page = self._bundle.repository.account_events(
                "mock", self._bundle.account_ref,
                after_sequence=self._fill_cursor, limit=1000,
            )
            for event in page.events:
                self._fill_cursor = max(self._fill_cursor, event.accepted_sequence)
                if (
                    event.run_id != self._bundle.run_id
                    or event.event_type != "FILL"
                    or not event.broker_execution_id
                    or event.broker_execution_id in self._seen_fill_ids
                    or event.quantity <= 0
                    or event.price <= 0
                ):
                    continue
                self._seen_fill_ids.add(event.broker_execution_id)
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
                        self._state = StrategyState(
                            status="cooldown",
                            cooldown_until=(event.occurred_at + timedelta(
                                seconds=int(self._config.cooldown_seconds),
                            )).isoformat(),
                            emitted_candidate_keys=self._state.emitted_candidate_keys,
                        )
            if not page.has_more:
                break

    def _state_matches_risk(self, risk: Any) -> bool:
        owned = {symbol: quantity for symbol, quantity in risk.owned_position_quantities}
        if self._state.status == "open":
            return owned == {self._state.symbol: int(self._state.position_quantity or 0)}
        return not owned

    def _restore_or_bootstrap(self) -> None:
        rows = self._store.load_documents(
            "execution_mock_automation_runner_current", self._bundle.account_ref, 1,
        )
        document = rows[0].get("document") if rows else None
        if isinstance(document, Mapping) and (
            document.get("version") == RUNNER_CHECKPOINT_VERSION
            and document.get("spec_id") == self._spec.spec_id
            and document.get("execution_run_id") == self._bundle.run_id
            and document.get("candidate_package_hash") == self._spec.candidate_package_hash
        ):
            if document.get("delivery_protocol") not in (None, OBSERVATION_DELIVERY_PROTOCOL):
                raise RuntimeError("unsupported_mock_observation_delivery_protocol")
            # Never silently discard a replacement reservation and reset its
            # anchor/budget through the legacy malformed-checkpoint bootstrap.
            registration = document.get('unfilled_registration')
            protection_exit=document.get('protection_exit')
            if protection_exit is not None:
                from kiwoom_monitor.application.mock_automation_risk import validate_mock_protection_exit
                validate_mock_protection_exit(protection_exit,self._spec,self._bundle.run_id)
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
                self._protection_review = deepcopy(notice)
                # A persisted flat resolution is historical evidence, never
                # authority to resume after restart or an ambiguous ACK.
                self._protection_review["state"] = "PENDING"
                self._protection_review.pop("resolved_risk_snapshot_id", None)
                self._protection_review.pop("resolved_risk_revision", None)
            try:
                self._cursor = max(0, int(document.get("input_cursor", 0)))
                self._fill_cursor = max(0, int(document.get("fill_cursor", 0)))
                self._checkpoint_revision = max(0, int(document.get("checkpoint_revision", 0)))
                state = dict(document.get("strategy_state", {}))
                state["emitted_candidate_keys"] = tuple(state.get("emitted_candidate_keys", ()))
                self._state = StrategyState(**state)
                self._bars = {
                    (frame.code, frame.observation_key): frame
                    for frame in (KrxMinuteBarFrame(**value) for value in document.get("bars", ()))
                }
                self._universes = [
                    CandidateUniverseFrame(**value) for value in document.get("universes", ())
                ]
                self._seen_fill_ids = set(str(value) for value in document.get("seen_fill_ids", ()))
                self._status = dict(document.get("status", self._status))
                if protection_exit is not None and self._status.get('first_protection_exit_state')=='LIQUIDATION_CONFIRMED':
                    self._status['first_protection_exit_state']='REVALIDATION_REQUIRED'
                    self._status.pop('protection_flat_risk_snapshot_id',None)
                self._delivery_protocol = document.get("delivery_protocol")
                self._frame_recovery_receipt = document.get("input_recovery")
                self._bootstrap_pending = bool(document.get("protection_resume_requires_bootstrap", False))
                if self._delivery_protocol is None:
                    self._frame_recovery = ObservationFrameRecovery(
                        self._cursor, str(self._spec.session_profile),
                        int(self._config.lookback_bars) + 2,
                        max(int(self._spec.maximum_data_gap_seconds or 0),
                            int(self._config.rank_window_seconds or 0)
                            + int(self._config.rank_max_gap_seconds or 0)), False,
                    )
                return
            except (TypeError, ValueError):
                if preflight is not None or attempt_plan is not None or notice is not None:
                    raise RuntimeError("unfilled_preflight_checkpoint_recovery_required")
                logger.warning("invalid automatic runner checkpoint ignored", exc_info=True)
        self._apply_new_fills()
        self._bootstrap()

    def _bootstrap(self) -> bool:
        page = self._store.load_observation_bootstrap(INPUT_KINDS, per_kind_limit=5000)
        if not page.ready:
            self._status = {
                "state": "WARMUP", "reason": "bootstrap_pending:" + page.reason,
                "orders_enabled": False,
            }
            return False
        seed = page.rows
        self._cursor = max((int(value.get("accepted_sequence", 0)) for value in seed), default=0)
        self._universes = list(replay_candidate_universe(seed))
        self._bars = {}
        if seed:
            for frame in replay_krx_minute_bars(
                seed, strict=True, session_profile=str(self._spec.session_profile),
            ):
                self._bars[(frame.code, frame.observation_key)] = frame
            for code in {frame.code for frame in self._bars.values()}:
                self._trim_bars(code)
            if self._universes:
                self._trim_universes(self._universes[-1].available_at)
        self._status = {
            "state": "WARMUP", "reason": "bootstrapped_without_historical_orders",
            "orders_enabled": False,
        }
        self._delivery_protocol = OBSERVATION_DELIVERY_PROTOCOL
        self._bootstrap_pending = False
        try:
            self._save_checkpoint()
        except Exception:
            self._bootstrap_pending = True
            raise
        return True

    def _trim_bars(self, code: str) -> None:
        self._bars = trim_bar_frames(self._bars, code, int(self._config.lookback_bars) + 2)

    def _trim_universes(self, latest_at: str) -> None:
        horizon = max(
            int(self._spec.maximum_data_gap_seconds or 0),
            int(self._config.rank_window_seconds or 0)
            + int(self._config.rank_max_gap_seconds or 0),
        )
        self._universes = trim_universe_frames(self._universes, latest_at, horizon, use_maximum=False)

    def _recover_frames(self, limit: int) -> None:
        recovery = self._frame_recovery
        if recovery.advance(self._store, INPUT_KINDS, limit):
            self._save_checkpoint(recovery=recovery)
            self._bars, self._universes = recovery.bars, recovery.universes
            self._delivery_protocol = OBSERVATION_DELIVERY_PROTOCOL
            self._frame_recovery_receipt = recovery.receipt()
            self._frame_recovery = None

    def _save_checkpoint(self, *, recovery=None, force: bool = False, protection_review=None) -> None:
        # The proposed flat proof may be persisted while the RAM cue stays
        # armed until ACK and post-ACK owner/Risk checks have all succeeded.
        notice = deepcopy(protection_review) if protection_review is not None else self._protection_snapshot()
        incomplete = self._bootstrap_pending or (self._frame_recovery is not None and recovery is None)
        if incomplete and notice is None:
            return  # Fills remain in their durable ledger and will be reapplied on restart.
        if recovery is not None and not recovery.complete:
            raise RuntimeError("cannot_checkpoint_partial_frame_recovery")
        content = {
            "version": RUNNER_CHECKPOINT_VERSION,
            "account_ref": self._bundle.account_ref,
            "spec_id": self._spec.spec_id,
            "candidate_package_hash": self._spec.candidate_package_hash,
            "execution_run_id": self._bundle.run_id,
            "input_cursor": self._cursor,
            "fill_cursor": self._fill_cursor,
            "pending_intent_id": str(self._status.get("pending_intent_id", "")),
            "strategy_state": self._state.to_dict(),
            "seen_fill_ids": sorted(self._seen_fill_ids),
            "universes": [asdict(value) for value in
                          (recovery.universes if recovery is not None else self._universes)],
            "bars": [asdict(value) for value in
                     (recovery.bars if recovery is not None else self._bars).values()],
            "status": deepcopy(self._status),
        }
        if recovery is not None:
            content["delivery_protocol"] = OBSERVATION_DELIVERY_PROTOCOL
            content["input_recovery"] = recovery.receipt()
        elif self._delivery_protocol is not None:
            content["delivery_protocol"] = self._delivery_protocol
            if self._frame_recovery_receipt is not None:
                content["input_recovery"] = deepcopy(self._frame_recovery_receipt)
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
                content["protection_resume_requires_bootstrap"] = self._bootstrap_pending
                if self._frame_recovery is not None:
                    content.pop("delivery_protocol", None)
                    content.pop("input_recovery", None)
        if not force and not self._checkpoint_pending and content == self._last_checkpoint_content:
            return  # Only this runner's acknowledged content can be a no-op.
        self._checkpoint_pending = True
        # Keep the existing monotonically increasing attempt generation, including
        # ambiguous commit outcomes. Pending/last-content track acknowledgement.
        self._checkpoint_revision += 1
        document = {
            **content, "checkpoint_revision": self._checkpoint_revision,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        self._store.upsert_documents(
            "execution_mock_automation_runner_current",
            [{"owner": self._bundle.account_ref, "key": "current", "document": document}],
        )
        self._last_checkpoint_content = content
        self._checkpoint_pending = False
