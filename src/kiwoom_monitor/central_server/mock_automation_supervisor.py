"""Own automatic mock-account bundle and strategy-runner lifecycles."""

from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from .market_observations import KST, as_kst

from kiwoom_monitor.application.mock_automation_admission import (
    MockAutomationDesiredState,
    admit_mock_automation,
    build_mock_automation_admission,
)
from kiwoom_monitor.application.mock_automation_execution import (
    emergency_stop_mock_automation,
    resume_mock_automation,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    record_mock_automation_recovery_from_risk,
)
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)

from .mock_automation_runner import MockAutomationRunner
from .mock_runtime import (
    MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION,
    MockAccountRuntimeMode,
    MockAutomationRuntimeContext,
)


logger = logging.getLogger(__name__)


class MockAutomationSupervisorError(RuntimeError):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class MockAutomationSupervisor:
    """Serialize account mode changes and own one runner per mock account."""

    def __init__(self, store: Any, credential_owner: Any, *, poll_seconds: float = 1.0, price_source=None,
                 now_provider: Callable[[], datetime] | None = None, leader_context_source=None) -> None:
        self._now = (lambda: as_kst(now_provider())) if now_provider else (lambda: datetime.now(KST))
        self._store = store
        self._owner = credential_owner
        self._repository = ForwardEvaluationRepository(store)
        self._poll_seconds = max(0.25, float(poll_seconds))
        self._price_source = price_source
        self._leader_context_source = leader_context_source
        self._runners: dict[str, MockAutomationRunner] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._restore_errors: dict[str, str] = {}
        self._restore_retry_after: dict[str, float] = {}
        self._restore_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._restore_stop = asyncio.Event()
        self._closing = False

    def observe_automation_price(self, symbol, price):
        if self._closing:
            return
        for runner in tuple(self._runners.values()):
            runner.observe_automation_price(symbol, price)

    def observe_leader_trade(self, payload, received_at, **source):
        if self._closing:
            return
        for runner in tuple(self._runners.values()):
            runner.observe_leader_trade(payload, received_at, **source)

    def position_shadow_observation(self,observation):
        """Enrich one borrowed account cut using its current registered owner only."""
        from dataclasses import fields
        from kiwoom_monitor.application.position_target import (
            PositionAccountObservation,PositionRunnerAccountObservation,PositionStudyRunnerAccountObservation)
        if observation is None:return None
        base=PositionAccountObservation(**{field.name:getattr(observation,field.name) for field in fields(PositionAccountObservation)})
        runner=self._runners.get(base.account.account_ref)
        if self._closing or runner is None:return base
        matches=getattr(getattr(runner._bundle,'monitor',None),'position_observation_matches',None)
        if not callable(matches) or not matches(base):return base
        state=runner.position_study_state(base)
        if self._closing or self._runners.get(base.account.account_ref) is not runner or state is None or not matches(base):return base
        getter=getattr(runner._bundle.monitor,'position_study_portfolio',None)
        portfolio=getter(base) if callable(getter) else None
        if self._closing or self._runners.get(base.account.account_ref) is not runner or not matches(base):return base
        if isinstance(portfolio,dict) and portfolio.get('version')=='native-position-study-portfolio/v1':
            policy_getter=getattr(runner,'position_study_policy',None)
            policy=policy_getter(base,state) if callable(policy_getter) else None
            if self._closing or self._runners.get(base.account.account_ref) is not runner or not matches(base):return base
            return PositionStudyRunnerAccountObservation(**{field.name:getattr(base,field.name) for field in fields(PositionAccountObservation)},
                runner_state=state,study_portfolio=portfolio,native_study_policy=policy)
        return PositionRunnerAccountObservation(**{field.name:getattr(base,field.name) for field in fields(PositionAccountObservation)},runner_state=state)

    async def start(self) -> None:
        if self._closing:
            raise RuntimeError("MOCK_AUTOMATION_SUPERVISOR_CLOSED")
        if self._restore_task is None:
            self._restore_task = asyncio.create_task(
                self._restore_loop(), name="mock-automation-restore",
            )

    async def close(self) -> None:
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="mock-automation-supervisor-close")
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        if self._restore_task is not None:
            self._restore_stop.set()
            await asyncio.shield(self._restore_task)
            self._restore_task = None
        runners = tuple(self._runners.values())
        results = await asyncio.gather(*(runner.close() for runner in runners), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        self._runners.clear()

    async def activate(
        self,
        *,
        credential_profile_id: str,
        account_ref: str,
        spec_id: str,
        expected_settings_revision: int,
        credential_revision: int,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(account_ref, asyncio.Lock())
        async with lock:
            if self._closing:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_SUPERVISOR_CLOSED", 503)
            existing_runner = self._runners.get(account_ref)
            if existing_runner is not None:
                if existing_runner.status["spec_id"] != spec_id:
                    raise MockAutomationSupervisorError("MOCK_AUTOMATION_ALREADY_ACTIVE")
                return self.status(account_ref, credential_profile_id=credential_profile_id)

            spec = await asyncio.to_thread(
                self._repository.load_mock_automation_spec, account_ref, spec_id,
            )
            if spec is None:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_SPEC_NOT_FOUND", 404)
            if (
                spec.account_scope.account_ref != account_ref
                or spec.credential_profile_id != credential_profile_id
            ):
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_SCOPE_MISMATCH")

            bundle = self._owner.bundle(credential_profile_id)
            if bundle is None or bundle.account_ref != account_ref:
                raise MockAutomationSupervisorError("PROFILE_RUNTIME_NOT_READY", 503)
            control = await asyncio.to_thread(
                self._repository.load_mock_automation_control, account_ref,
            )
            if control is not None and (
                control.active_spec_id != spec_id
                or control.desired_state is not MockAutomationDesiredState.RUNNING
            ):
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_EXPLICIT_HANDOFF_REQUIRED")
            requested_at = self._now()
            admission = await asyncio.to_thread(
                self._repository.load_mock_automation_admission_for_spec, account_ref, spec_id,
            )
            if admission is None:
                admission = build_mock_automation_admission(spec, requested_at=requested_at)
            control_revision = control.control_revision if control is not None else 1
            context = MockAutomationRuntimeContext(
                MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION,
                account_ref,
                admission.execution_run_id,
                spec_id,
                admission.admission_id,
                control_revision,
                credential_revision,
            )
            if bundle.runtime_mode is MockAccountRuntimeMode.MANUAL:
                bundle = await self._owner.switch_execution_mode(
                    credential_profile_id,
                    MockAccountRuntimeMode.AUTOMATIC,
                    expected_settings_revision=expected_settings_revision,
                    credential_revision=credential_revision,
                    automation_context=context,
                )
            elif bundle.automation_context != context:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_RUNTIME_CONTEXT_MISMATCH")

            try:
                await asyncio.to_thread(
                    admit_mock_automation,
                    self._repository,
                    self._repository,
                    bundle.runtime,
                    account_ref=account_ref,
                    spec_id=spec_id,
                    requested_at=requested_at,
                )
                recovery = await bundle.monitor.refresh_recovery()
                risk = await asyncio.to_thread(
                    self._repository.load_latest_mock_automation_risk, account_ref,
                )
                if risk is None:
                    raise RuntimeError("MOCK_AUTOMATION_RISK_MISSING")
                await asyncio.to_thread(
                    record_mock_automation_recovery_from_risk,
                    self._repository,
                    bundle.runtime,
                    recovery,
                    risk,
                    account_ref=account_ref,
                    spec_id=spec_id,
                )
                runner = MockAutomationRunner(
                    self._store, bundle, spec, poll_seconds=self._poll_seconds, price_source=self._price_source,
                    now_provider=self._now,
                    leader_context_source=self._leader_context_source,
                )
                await runner.start()
            except Exception:
                bundle.runtime.set_new_orders_enabled(False)
                raise
            self._runners[account_ref] = runner
            self._restore_errors.pop(account_ref, None)
            self._restore_retry_after.pop(account_ref, None)
            return self.status(account_ref, credential_profile_id=credential_profile_id)

    async def stop(
        self,
        *,
        credential_profile_id: str,
        account_ref: str,
        spec_id: str,
        expected_control_revision: int,
        reason: str,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(account_ref, asyncio.Lock())
        async with lock:
            bundle = self._require_automatic_bundle(
                credential_profile_id, account_ref, spec_id,
            )
            control = await asyncio.to_thread(
                self._repository.load_mock_automation_control, account_ref,
            )
            if control is None or control.control_revision != expected_control_revision:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT")
            await asyncio.to_thread(
                emergency_stop_mock_automation,
                self._repository,
                bundle.runtime,
                account_ref=account_ref,
                spec_id=spec_id,
                stopped_at=self._now(),
                reason=reason,
            )
            return self.status(account_ref, credential_profile_id=credential_profile_id)

    async def resume(
        self,
        *,
        credential_profile_id: str,
        account_ref: str,
        spec_id: str,
        expected_control_revision: int,
        expected_settings_revision: int,
        credential_revision: int,
        reason: str,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(account_ref, asyncio.Lock())
        async with lock:
            control = await asyncio.to_thread(
                self._repository.load_mock_automation_control, account_ref,
            )
            if control is None or control.control_revision != expected_control_revision:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT")
            if (
                control.active_spec_id != spec_id
                or control.desired_state is not MockAutomationDesiredState.STOPPED
            ):
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_NOT_STOPPED")
            spec = await asyncio.to_thread(
                self._repository.load_mock_automation_spec, account_ref, spec_id,
            )
            admission = await asyncio.to_thread(
                self._repository.load_mock_automation_admission_for_spec, account_ref, spec_id,
            )
            if spec is None or admission is None:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_ADMISSION_MISSING", 404)
            if spec.credential_profile_id != credential_profile_id:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_SCOPE_MISMATCH")
            bundle = self._owner.bundle(credential_profile_id)
            if bundle is None or bundle.account_ref != account_ref:
                raise MockAutomationSupervisorError("PROFILE_RUNTIME_NOT_READY", 503)
            context = MockAutomationRuntimeContext(
                MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION,
                account_ref,
                admission.execution_run_id,
                spec_id,
                admission.admission_id,
                control.control_revision,
                credential_revision,
            )
            if bundle.runtime_mode is MockAccountRuntimeMode.MANUAL:
                bundle = await self._owner.switch_execution_mode(
                    credential_profile_id,
                    MockAccountRuntimeMode.AUTOMATIC,
                    expected_settings_revision=expected_settings_revision,
                    credential_revision=credential_revision,
                    automation_context=context,
                )
            elif bundle.automation_context != context:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_RUNTIME_CONTEXT_MISMATCH")
            recovery = await bundle.monitor.refresh_recovery()
            risk = await asyncio.to_thread(
                self._repository.load_latest_mock_automation_risk, account_ref,
            )
            if risk is None:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_RISK_MISSING", 503)
            await asyncio.to_thread(
                record_mock_automation_recovery_from_risk,
                self._repository,
                bundle.runtime,
                recovery,
                risk,
                account_ref=account_ref,
                spec_id=spec_id,
            )
            await asyncio.to_thread(
                resume_mock_automation,
                self._repository,
                bundle.runtime,
                account_ref=account_ref,
                spec_id=spec_id,
                resumed_at=self._now(),
                reason=reason,
            )
            if account_ref not in self._runners:
                try:
                    runner = MockAutomationRunner(
                        self._store, bundle, spec, poll_seconds=self._poll_seconds, price_source=self._price_source,
                        now_provider=self._now,
                        leader_context_source=self._leader_context_source,
                    )
                    await runner.start()
                except Exception:
                    bundle.runtime.set_new_orders_enabled(False)
                    raise
                self._runners[account_ref] = runner
                self._restore_errors.pop(account_ref, None)
                self._restore_retry_after.pop(account_ref, None)
            return self.status(account_ref, credential_profile_id=credential_profile_id)

    def status(self, account_ref: str, *, credential_profile_id: str = "") -> dict[str, Any]:
        control = self._repository.load_mock_automation_control(account_ref)
        runner = self._runners.get(account_ref)
        bundle = self._owner.bundle(credential_profile_id) if credential_profile_id else None
        return {
            "version": "mock_automation_supervisor_status/v1",
            "account_ref": account_ref,
            "credential_profile_id": credential_profile_id,
            "runtime_mode": bundle.runtime_mode.value if bundle is not None else None,
            "control": control.to_dict() if control is not None else None,
            "runner": runner.status if runner is not None else None,
            "restore_error": self._restore_errors.get(account_ref, ""),
        }

    async def publish_native_unfilled_population(self, document):
        """Existing account lock/runner only; never activate or restore for a packet."""
        from kiwoom_monitor.application.research_evaluation import unfilled_population_publication_from_dict
        from .observation_frame_recovery import drain_thread_call
        document=deepcopy(document)
        # Pure bounded decoding outside the account control lock. No market/DB read.
        await drain_thread_call(unfilled_population_publication_from_dict,document)
        account_ref=document['account_ref']
        lock=self._locks.setdefault(account_ref,asyncio.Lock())
        async with lock:
            if self._closing:raise MockAutomationSupervisorError('MOCK_AUTOMATION_SUPERVISOR_CLOSED',503)
            bundle=self._require_automatic_bundle(document['credential_profile_id'],account_ref,document['spec_id'])
            if bundle.automation_context.control_revision!=document['expected_control_revision']:
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT')
            runner=self._runners.get(account_ref)
            if runner is None or bundle.run_id!=document['execution_run_id']:
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_RUNTIME_NOT_ACTIVE')
            prior=runner.status.get('unfilled_population_source',{})
            if prior.get('publication_ref')!=document['publication_ref']:
                control=await drain_thread_call(self._repository.load_mock_automation_control,account_ref)
                if (control is None or control.desired_state is not MockAutomationDesiredState.RUNNING
                        or (control.active_spec_id,control.execution_run_id,control.control_revision)!=(
                            document['spec_id'],document['execution_run_id'],document['expected_control_revision'])):
                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT')
            return await drain_thread_call(runner.offer_native_unfilled_publication,document)

    def _require_automatic_bundle(
        self, credential_profile_id: str, account_ref: str, spec_id: str,
    ) -> Any:
        bundle = self._owner.bundle(credential_profile_id)
        if bundle is None or bundle.account_ref != account_ref:
            raise MockAutomationSupervisorError("PROFILE_RUNTIME_NOT_READY", 503)
        context = bundle.automation_context
        if (
            bundle.runtime_mode is not MockAccountRuntimeMode.AUTOMATIC
            or context is None
            or context.spec_id != spec_id
        ):
            raise MockAutomationSupervisorError("MOCK_AUTOMATION_RUNTIME_NOT_ACTIVE")
        return bundle

    async def _restore_loop(self) -> None:
        while not self._restore_stop.is_set():
            try:
                await self._restore_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("automatic mock runtime restore scan failed")
            try:
                await asyncio.wait_for(self._restore_stop.wait(), timeout=2.0)
            except TimeoutError:
                pass

    async def _restore_once(self) -> None:
        for binding in self._owner.account_bindings():
            account_ref = binding.scope.account_ref
            if account_ref in self._runners:
                continue
            if time.monotonic() < self._restore_retry_after.get(account_ref, 0.0):
                continue
            control = await asyncio.to_thread(
                self._repository.load_mock_automation_control, account_ref,
            )
            if control is None or control.desired_state is not MockAutomationDesiredState.RUNNING:
                continue
            profile_id = binding.credential_profile_id
            bundle = self._owner.bundle(profile_id)
            credential_revision = self._owner.active_revision(profile_id)
            if bundle is None or credential_revision is None:
                continue
            settings = await asyncio.to_thread(
                self._store.load_account_settings, binding.scope.to_dict(),
            )
            try:
                await self.activate(
                    credential_profile_id=profile_id,
                    account_ref=account_ref,
                    spec_id=control.active_spec_id,
                    expected_settings_revision=int(settings["revision"]),
                    credential_revision=int(credential_revision),
                )
            except Exception as error:
                bundle.runtime.set_new_orders_enabled(False)
                detail = str(error)
                if self._restore_errors.get(account_ref) != detail:
                    logger.error(
                        "automatic mock runtime restore remains fail-closed account=%s error=%s",
                        account_ref, type(error).__name__,
                    )
                self._restore_errors[account_ref] = detail
                self._restore_retry_after[account_ref] = time.monotonic() + 30.0
