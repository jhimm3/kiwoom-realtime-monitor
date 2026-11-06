"""Own automatic mock-account bundle and strategy-runner lifecycles."""

from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from .market_observations import KST, as_kst

from kiwoom_monitor.application.mock_automation_admission import (
    MockAutomationDesiredState,
    admit_mock_automation,
    admit_mock_automation_account_selection,
    build_mock_automation_admission,
    prepare_mock_automation_account_replacement,
)
from kiwoom_monitor.application.mock_automation_execution import (
    commit_mock_automation_account_replacement,
    emergency_stop_mock_automation,
    resume_mock_automation,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    record_mock_automation_recovery_from_risk,
)
from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)

if TYPE_CHECKING:
    from .mock_automation_runner import MockAutomationRunner
from .observation_frame_recovery import drain_thread_call
from .mock_runtime import (
    MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION,
    MockAccountRuntimeMode,
    MockAutomationRuntimeContext,
)


logger = logging.getLogger(__name__)


def strategy_implementation_unavailable(error: BaseException) -> bool:
    return isinstance(error, ModuleNotFoundError) and error.name in {
        'kiwoom_monitor.application.close_mock_execution',
        'kiwoom_monitor.application.panic_mock_execution',
    }


class MockAutomationSupervisorError(RuntimeError):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class MockAutomationSupervisor:
    """Serialize account mode changes and own one runner per mock account."""

    def __init__(self, store: Any, credential_owner: Any, *, poll_seconds: float = 1.0, price_source=None,
                 now_provider: Callable[[], datetime] | None = None, leader_context_source=None,
                 expected_implementation_hash=None) -> None:
        self._now = (lambda: as_kst(now_provider())) if now_provider else (lambda: datetime.now(KST))
        self._store = store
        self._owner = credential_owner
        self._repository = ForwardEvaluationRepository(store)
        self._poll_seconds = max(0.25, float(poll_seconds))
        self._price_source = price_source
        self._leader_context_source = leader_context_source
        self._expected_implementation_hash = expected_implementation_hash
        self._runners: dict[str, MockAutomationRunner] = {}
        self._preparing_runners: set[MockAutomationRunner] = set()
        # The current map is withdrawn before bundle replacement. Keep its
        # actual close owned until both the callback and shutdown can join it.
        self._runner_close_tasks: dict[MockAutomationRunner, asyncio.Task[None]] = {}
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
        first_error = None
        for runner in tuple(self._runners.values()):
            for episode in runner.episodes:
                try:
                    episode.observe_automation_price(symbol, price)
                except Exception as error:
                    if first_error is None:
                        first_error = error
        if first_error is not None:
            raise first_error

    def observe_leader_trade(self, payload, received_at, **source):
        if self._closing:
            return
        first_error = None
        for runner in tuple(self._runners.values()):
            for episode in runner.episodes:
                try:
                    episode.observe_leader_trade(payload, received_at, **source)
                except Exception as error:
                    if first_error is None:
                        first_error = error
        if first_error is not None:
            raise first_error

    def position_shadow_observation(self,observation):
        """Enrich one borrowed account cut using its current registered owner only."""
        from dataclasses import fields
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        if observation is None:return None
        base=PositionAccountObservation(**{field.name:getattr(observation,field.name) for field in fields(PositionAccountObservation)})
        runner=self._runners.get(base.account.account_ref)
        if self._closing or runner is None:return base
        return runner.episode.position_shadow_observation(
            base, is_current=lambda: not self._closing and self._runners.get(base.account.account_ref) is runner,
        )

    async def start(self) -> None:
        if self._closing:
            raise RuntimeError("MOCK_AUTOMATION_SUPERVISOR_CLOSED")
        if self._restore_task is None:
            self._restore_task = asyncio.create_task(
                self._restore_loop(), name="mock-automation-restore",
            )

    async def _close_runner(self, runner, *, withdraw=True) -> None:
        account_ref = runner.episode.account_ref
        if withdraw and self._runners.get(account_ref) is runner:
            self._runners.pop(account_ref)
        task = self._runner_close_tasks.get(runner)
        if task is None:
            task = asyncio.create_task(runner.close(), name="mock-automation-runner-close")
            self._runner_close_tasks[runner] = task
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        await asyncio.shield(task)
        if self._runners.get(account_ref) is runner:
            self._runners.pop(account_ref)

    async def _join_runner_closes(self, account_ref) -> None:
        for runner, task in tuple(self._runner_close_tasks.items()):
            if runner.episode.account_ref == account_ref:
                await asyncio.shield(task)

    async def bundle_changed(self, profile_id, bundle) -> None:
        # The owner can call this while activate owns the account lock during a
        # mode switch; taking that same lock here would deadlock.
        for runner in tuple(dict.fromkeys((*self._runners.values(), *self._preparing_runners))):
            if runner.episode.credential_profile_id == profile_id and not runner.episode.is_bound_to(bundle):
                await self._close_runner(runner)
        for runner, task in tuple(self._runner_close_tasks.items()):
            if runner.episode.credential_profile_id == profile_id and not runner.episode.is_bound_to(bundle):
                await asyncio.shield(task)

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
        # Accepted account operations may still be in native admission/recovery.
        # Join their existing serialization boundary before releasing services.
        for lock in tuple(self._locks.values()):
            async with lock:
                pass
        runners = tuple(dict.fromkeys((*self._runners.values(), *self._preparing_runners, *self._runner_close_tasks)))
        results = await asyncio.gather(*(self._close_runner(runner, withdraw=False) for runner in runners), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        self._runners.clear()
        self._runner_close_tasks.clear()

    async def activate(
        self,
        *,
        credential_profile_id: str,
        account_ref: str,
        spec_id: str,
        expected_settings_revision: int,
        credential_revision: int,
        account_selection_id: str | None = None,
        _restore_stopped_management: bool = False,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(account_ref, asyncio.Lock())
        async with lock:
            if self._closing:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_SUPERVISOR_CLOSED", 503)
            await self._join_runner_closes(account_ref)
            existing_runner = self._runners.get(account_ref)
            if existing_runner is not None:
                if existing_runner.episode.is_bound_to(self._owner.bundle(credential_profile_id)):
                    if (existing_runner.episode.status["spec_id"] != spec_id
                            or getattr(existing_runner, 'account_selection_id', None) != account_selection_id):
                        raise MockAutomationSupervisorError("MOCK_AUTOMATION_ALREADY_ACTIVE")
                    return self.status(account_ref, credential_profile_id=credential_profile_id)
                await self._close_runner(existing_runner)

            spec = await drain_thread_call(
                self._repository.load_mock_automation_spec, account_ref, spec_id,
            )
            if spec is None:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_SPEC_NOT_FOUND", 404)
            if (
                spec.account_scope.account_ref != account_ref
                or spec.credential_profile_id != credential_profile_id
            ):
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_SCOPE_MISMATCH")

            selection = None
            if account_selection_id is not None:
                selection = await drain_thread_call(
                    self._repository.load_mock_automation_account_selection, account_ref, account_selection_id,
                )
                if selection is None:
                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_ACCOUNT_SELECTION_NOT_FOUND', 404)
                self._validate_account_selection(selection, spec_id, credential_profile_id)

            bundle = self._owner.bundle(credential_profile_id)
            if bundle is None or bundle.account_ref != account_ref:
                raise MockAutomationSupervisorError("PROFILE_RUNTIME_NOT_READY", 503)
            control = await drain_thread_call(
                self._repository.load_mock_automation_control, account_ref,
            )
            if control is not None and (
                control.active_spec_id != spec_id
                or (control.desired_state is not MockAutomationDesiredState.RUNNING
                    and not (_restore_stopped_management and selection is not None
                             and control.desired_state is MockAutomationDesiredState.STOPPED))
                or getattr(control, 'account_selection', None) != selection
            ):
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_EXPLICIT_HANDOFF_REQUIRED")
            requested_at = self._now()
            admission = await drain_thread_call(
                self._repository.load_mock_automation_admission_for_spec, account_ref, spec_id,
            )
            if admission is None:
                if _restore_stopped_management:
                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_ADMISSION_MISSING', 404)
                admission = build_mock_automation_admission(spec, requested_at=requested_at,
                    **({'account_selection': selection} if selection is not None else {}))
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
            else:
                await self._owner.require_execution_settings(credential_profile_id,
                    expected_settings_revision=expected_settings_revision, credential_revision=credential_revision)

            previous_execution_inputs = getattr(bundle.monitor, 'automation_execution_inputs_enabled', False)
            try:
                if selection is not None:
                    if _restore_stopped_management or control is not None and (getattr(control, 'entry_resume_required', False)
                            or selection.version == 'mock_automation_account_selection/v2'):
                        admissions = await drain_thread_call(self._load_managed_account_admissions,
                            bundle, selection, control)
                        await drain_thread_call(bundle.runtime.start, new_orders_enabled=False)
                    else:
                        admissions, _, control = await drain_thread_call(
                            admit_mock_automation_account_selection, self._repository, bundle.runtime,
                            account_ref=account_ref, selection_id=selection.selection_id, requested_at=requested_at,
                            expected_control_revision=control.control_revision if control is not None else 0,
                            expected_implementation_hash=self._account_implementation_hash,
                        )
                    bundle.refresh_automation_control(control)
                elif control is not None and getattr(control, "entry_resume_required", False):
                    # Reconnect management of an already admitted run. A new
                    # key does not issue another admission or entry approval.
                    receipt = await drain_thread_call(self._repository.load_mock_automation_lease_for_admission,
                        account_ref, admission.admission_id)
                    if receipt is None:
                        raise MockAutomationSupervisorError("MOCK_AUTOMATION_ADMISSION_MISSING", 404)
                    bundle.runtime.set_new_orders_enabled(False)
                else:
                    await drain_thread_call(
                        admit_mock_automation,
                        self._repository,
                        self._repository,
                        bundle.runtime,
                        account_ref=account_ref,
                        spec_id=spec_id,
                        requested_at=requested_at,
                    )
                if selection is not None:
                    await self._recover_account_selection(bundle, selection)
                    runner = await self._assemble_runner(bundle, spec, credential_profile_id,
                        selection=selection, member_admissions=admissions)
                else:
                    recovery = await bundle.monitor.refresh_recovery()
                    risk = await drain_thread_call(
                        self._repository.load_latest_mock_automation_risk, account_ref,
                    )
                    if risk is None:
                        raise RuntimeError("MOCK_AUTOMATION_RISK_MISSING")
                    await drain_thread_call(
                        record_mock_automation_recovery_from_risk,
                        self._repository,
                        bundle.runtime,
                        recovery,
                        risk,
                        account_ref=account_ref,
                        spec_id=spec_id,
                    )
                    runner = await self._assemble_runner(bundle, spec, credential_profile_id)
            except BaseException:
                if self._owner.bundle(credential_profile_id) is bundle:
                    bundle.runtime.set_new_orders_enabled(False)
                    if selection is not None:
                        bundle.monitor.set_automation_execution_inputs_enabled(previous_execution_inputs, request_recovery=False)
                raise
            # No await between the final identity fence and publication. A
            # concurrent close has already set _closing before taking its cut.
            self._runner_close_tasks = {old: task for old, task in self._runner_close_tasks.items()
                                        if old.episode.account_ref != account_ref}
            self._runners[account_ref] = runner
            self._restore_errors.pop(account_ref, None)
            self._restore_retry_after.pop(account_ref, None)
            return self.status(account_ref, credential_profile_id=credential_profile_id)

    async def replace_selection(self, *, credential_profile_id: str, account_ref: str, spec_id: str,
            account_selection_id: str, expected_control_revision: int, expected_settings_revision: int,
            credential_revision: int, reason: str) -> dict[str, Any]:
        """Keep the account owner and old Episodes while explicitly replacing entry members."""
        from .mock_automation_episode import MockAutomationEpisode
        from .mock_automation_runner import MockAutomationRunner
        from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_account_recovery_from_cut

        async with self._locks.setdefault(account_ref, asyncio.Lock()):
            if self._closing:
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_SUPERVISOR_CLOSED', 503)
            bundle = self._require_automatic_bundle(credential_profile_id, account_ref, spec_id)
            await self._owner.require_execution_settings(credential_profile_id,
                expected_settings_revision=expected_settings_revision, credential_revision=credential_revision)
            old = self._runners.get(account_ref)
            if old is None or not old.episode.is_bound_to(bundle):
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_RUNTIME_NOT_ACTIVE')
            before = await drain_thread_call(self._repository.load_mock_automation_control, account_ref)
            if before is None:
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT')
            already = (before.account_selection is not None
                       and before.account_selection.selection_id == account_selection_id)
            if (before.control_revision not in ({expected_control_revision, expected_control_revision + 1}
                                               if already else {expected_control_revision})):
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT')
            if already and old.account_selection_id == account_selection_id:
                return self._status_from_control(account_ref, credential_profile_id=credential_profile_id, control=before)
            was_scheduled = old._has_scheduled_task()
            await old.pause_for_replacement()
            runner = None
            created = []
            applied = None
            try:
                selection, admissions, _, prepared_control = await drain_thread_call(
                    prepare_mock_automation_account_replacement, self._repository, bundle.runtime,
                    account_ref=account_ref, selection_id=account_selection_id, requested_at=self._now(),
                    expected_control_revision=expected_control_revision,
                    expected_implementation_hash=self._account_implementation_hash)
                await bundle.monitor.prepare_account_selection_recovery(selection.selection_id, prepared_control.control_revision)
                cut = bundle.monitor.latest_automation_execution_cut
                if cut is None:
                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_ACCOUNT_RECOVERY_CUT_MISSING', 503)
                await drain_thread_call(record_mock_automation_account_recovery_from_cut,
                    self._repository, bundle.runtime, cut, account_ref=account_ref, selection_id=selection.selection_id,
                    prospective_selection=selection, expected_control_revision=prepared_control.control_revision)
                members = []
                for admission in admissions:
                    episode = old.episode_for_spec(admission.spec_id)
                    if episode is None:
                        spec = await drain_thread_call(self._repository.load_mock_automation_spec,
                            account_ref, admission.spec_id, require_canonical_document=True)
                        episode = MockAutomationEpisode(self._store, bundle, spec, member_admission=admission,
                            proposed_selection=selection, price_source=self._price_source,
                            leader_context_source=self._leader_context_source, now_provider=self._now)
                        created.append(episode)
                        await drain_thread_call(episode.prepare_restore)
                    members.append(episode)
                runner = MockAutomationRunner(members[0], member_episodes=members[1:], monitor=bundle.monitor,
                    poll_seconds=self._poll_seconds, execution_order=selection.execution_order_spec_ids,
                    account_selection=selection, borrowed_episodes=old.episodes)
                self._preparing_runners.add(runner)
                if self._closing or self._owner.bundle(credential_profile_id) is not bundle:
                    raise MockAutomationSupervisorError('PROFILE_RUNTIME_NOT_READY', 503)
                try:
                    applied = await drain_thread_call(commit_mock_automation_account_replacement,
                        self._repository, bundle.runtime, account_ref=account_ref,
                        selection_id=selection.selection_id, requested_at=self._now(),
                        expected_control_revision=expected_control_revision, reason=reason)
                except BaseException:
                    # A durable COMMIT whose response was lost is read back by its
                    # immutable selection and exact successor revision; never CAS twice.
                    current = await drain_thread_call(self._repository.load_mock_automation_control, account_ref)
                    if (current is None or current.account_selection != selection
                            or current.control_revision != expected_control_revision + 1):
                        raise
                    applied = current
                bundle.refresh_automation_control(applied)
                for episode in members:
                    episode.apply_account_selection(applied)
                runner.adopt_episodes_from(old)
                self._runners[account_ref] = runner
                runner.attach_inputs()
                await runner.start()
                self._restore_errors.pop(account_ref, None)
                return self._status_from_control(account_ref, credential_profile_id=credential_profile_id, control=applied)
            except BaseException:
                if applied is None:
                    if runner is not None:
                        await runner.close()  # Only new members are owned before the CAS.
                    else:
                        for episode in created:
                            episode.begin_close(retire_wake=lambda: None)
                            await episode.finish_close(scheduled_task_was_present=False)
                    if not self._closing and self._owner.bundle(credential_profile_id) is bundle:
                        current = await drain_thread_call(self._repository.load_mock_automation_control, account_ref)
                        if current is not None and current.account_selection == before.account_selection:
                            await self._recover_existing_control(bundle, current)
                            bundle.refresh_automation_control(current)
                            old.bind_execution_callbacks()
                            if was_scheduled:
                                await old.start()
                        else:
                            self._restore_errors[account_ref] = 'MOCK_AUTOMATION_REPLACEMENT_READBACK_REQUIRED'
                else:
                    self._restore_errors[account_ref] = 'MOCK_AUTOMATION_REPLACEMENT_ATTACH_REQUIRED'
                raise
            finally:
                if runner is not None:
                    self._preparing_runners.discard(runner)

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
            management = getattr(bundle, 'selected_management_context', None)
            repository = (ForwardEvaluationRepository(self._store, selected_management_context=management)
                          if management is not None else self._repository)
            control = await drain_thread_call(
                repository.load_mock_automation_control, account_ref,
            )
            if control is None or control.control_revision != expected_control_revision:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT")
            await drain_thread_call(
                emergency_stop_mock_automation,
                repository,
                bundle.runtime,
                account_ref=account_ref,
                spec_id=spec_id,
                stopped_at=self._now(),
                reason=reason,
            )
            control = await drain_thread_call(
                repository.load_mock_automation_control, account_ref,
            )
            bundle.refresh_automation_control(control)
            return self._status_from_control(
                account_ref, credential_profile_id=credential_profile_id, control=control,
            )

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
            if self._closing:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_SUPERVISOR_CLOSED", 503)
            await self._join_runner_closes(account_ref)
            existing_runner = self._runners.get(account_ref)
            if existing_runner is not None and not existing_runner.episode.is_bound_to(self._owner.bundle(credential_profile_id)):
                await self._close_runner(existing_runner)
            control = await drain_thread_call(
                self._repository.load_mock_automation_control, account_ref,
            )
            if control is None or control.control_revision != expected_control_revision:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT")
            if (
                control.active_spec_id != spec_id
                or (control.desired_state is not MockAutomationDesiredState.STOPPED
                    and not (control.desired_state is MockAutomationDesiredState.RUNNING
                             and getattr(control, "entry_resume_required", False)))
            ):
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_NOT_STOPPED")
            spec = await drain_thread_call(
                self._repository.load_mock_automation_spec, account_ref, spec_id,
            )
            admission = await drain_thread_call(
                self._repository.load_mock_automation_admission_for_spec, account_ref, spec_id,
            )
            if spec is None or admission is None:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_ADMISSION_MISSING", 404)
            if spec.credential_profile_id != credential_profile_id:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_SCOPE_MISMATCH")
            bundle = self._owner.bundle(credential_profile_id)
            if bundle is None or bundle.account_ref != account_ref:
                raise MockAutomationSupervisorError("PROFILE_RUNTIME_NOT_READY", 503)
            if bundle.runtime_mode is MockAccountRuntimeMode.AUTOMATIC:
                await self._owner.require_execution_settings(credential_profile_id,
                    expected_settings_revision=expected_settings_revision, credential_revision=credential_revision)
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
                prior = bundle.automation_context
                if prior is None or prior != replace(context, control_revision=prior.control_revision):
                    raise MockAutomationSupervisorError("MOCK_AUTOMATION_RUNTIME_CONTEXT_MISMATCH")
                bundle.refresh_automation_control(control)
            selection = getattr(control, 'account_selection', None)
            if selection is not None:
                self._validate_account_selection(selection, spec_id, credential_profile_id)
                if (runner := self._runners.get(account_ref)) is not None:
                    await self._close_runner(runner)
                def load_admissions():
                    return tuple(self._repository.load_mock_automation_admission_for_spec(account_ref, identity)
                                 for identity in selection.member_spec_ids)
                admissions = await drain_thread_call(load_admissions)
                if any(value is None for value in admissions):
                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_ADMISSION_MISSING', 404)
                previous_execution_inputs = getattr(bundle.monitor, 'automation_execution_inputs_enabled', False)
                try:
                    recovery, risk = await self._recover_account_selection(bundle, selection)
                    async def commit_account_resume(episodes):
                        reviews = {}
                        for episode in episodes:
                            notice = episode._protection_snapshot()
                            if notice is not None:
                                resolved = notice
                                if notice['state'] == 'PENDING':
                                    resolved = await drain_thread_call(episode.resolve_protection_review,
                                        expected_revision=notice['revision'], recovery=recovery, risk=risk,
                                        planned_resume_control=control)
                                if resolved['state'] != 'RESOLVED_FLAT':
                                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_PROTECTION_REVIEW_REQUIRED')
                                if resolved.get('planned_review_resume_id') is not None:
                                    reviews[episode.spec_id] = resolved['planned_review_resume_id']
                        resumed = await drain_thread_call(resume_mock_automation, self._repository, bundle.runtime,
                            account_ref=account_ref, spec_id=spec_id, resumed_at=self._now(), reason=reason,
                            **({'planned_review_resume_ids': reviews} if reviews else {}))
                        bundle.refresh_automation_control(resumed)
                    runner = await self._assemble_runner(bundle, spec, credential_profile_id,
                        selection=selection, member_admissions=admissions, before_attach=commit_account_resume)
                except BaseException:
                    if self._owner.bundle(credential_profile_id) is bundle:
                        bundle.runtime.set_new_orders_enabled(False)
                        bundle.monitor.set_automation_execution_inputs_enabled(previous_execution_inputs, request_recovery=False)
                    raise
                self._runner_close_tasks = {old: task for old, task in self._runner_close_tasks.items()
                                            if old.episode.account_ref != account_ref}
                self._runners[account_ref] = runner
                self._restore_errors.pop(account_ref, None)
                self._restore_retry_after.pop(account_ref, None)
                return self.status(account_ref, credential_profile_id=credential_profile_id)
            planned_resume = getattr(spec, "execution_plan_mode", None) == LEADER_NATIVE_PLAN_MODE
            if planned_resume and (runner := self._runners.get(account_ref)) is not None:
                # Drain the STOPPED episode before acknowledging its review.
                # The existing account monitor and native lease stay owned.
                await self._close_runner(runner)
            recovery = await bundle.monitor.refresh_recovery()
            risk = await drain_thread_call(
                self._repository.load_latest_mock_automation_risk, account_ref,
            )
            if risk is None:
                raise MockAutomationSupervisorError("MOCK_AUTOMATION_RISK_MISSING", 503)
            await drain_thread_call(
                record_mock_automation_recovery_from_risk,
                self._repository,
                bundle.runtime,
                recovery,
                risk,
                account_ref=account_ref,
                spec_id=spec_id,
            )
            async def commit_resume(episode=None):
                receipt = None
                if episode is not None:
                    notice = episode._protection_snapshot()
                    if notice is not None:
                        resolved = await drain_thread_call(
                            episode.resolve_protection_review, expected_revision=notice["revision"],
                            recovery=recovery, risk=risk, planned_resume_control=control,
                        )
                        receipt = resolved["planned_review_resume_id"]
                resumed = await drain_thread_call(
                    resume_mock_automation, self._repository, bundle.runtime, account_ref=account_ref,
                    spec_id=spec_id, resumed_at=self._now(), reason=reason,
                    **({"planned_review_resume_id": receipt} if receipt is not None else {}),
                )
                bundle.refresh_automation_control(resumed)

            if not planned_resume:
                await commit_resume()
            if account_ref not in self._runners:
                try:
                    runner = await self._assemble_runner(bundle, spec, credential_profile_id,
                        **({"before_attach": commit_resume} if planned_resume else {}))
                except BaseException:
                    if self._owner.bundle(credential_profile_id) is bundle:
                        bundle.runtime.set_new_orders_enabled(False)
                    raise
                self._runner_close_tasks = {old: task for old, task in self._runner_close_tasks.items()
                                            if old.episode.account_ref != account_ref}
                self._runners[account_ref] = runner
                self._restore_errors.pop(account_ref, None)
                self._restore_retry_after.pop(account_ref, None)
            return self.status(account_ref, credential_profile_id=credential_profile_id)

    def _validate_account_selection(self, selection, spec_id, credential_profile_id):
        if (selection.primary_spec_id != spec_id
                or selection.credential_profile_id != credential_profile_id):
            raise MockAutomationSupervisorError('MOCK_AUTOMATION_SCOPE_MISMATCH')
        if selection.execution_order_spec_ids is None:
            raise MockAutomationSupervisorError('MOCK_AUTOMATION_EXPLICIT_MEMBER_EXECUTION_ORDER_REQUIRED')

    def _account_implementation_hash(self, session_profile):
        if self._expected_implementation_hash is not None:
            return self._expected_implementation_hash(session_profile)
        from kiwoom_monitor.application.research_implementation import research_implementation_hash
        return research_implementation_hash(session_profile)

    def _load_managed_account_admissions(self, bundle, selection, control):
        from kiwoom_monitor.application.mock_automation_admission import MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION
        admissions = []
        with self._repository.mock_automation_read_scope() as reader:
            args = {} if reader is None else {'_reader': reader}
            if (self._repository.load_mock_automation_control(bundle.account_ref, **args) != control
                    or self._repository.load_mock_automation_account_selection(bundle.account_ref, selection.selection_id, **args) != selection):
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT')
            for identity in selection.member_spec_ids:
                spec = self._repository.load_mock_automation_spec(bundle.account_ref, identity,
                    require_canonical_document=True, **args)
                if spec is None:
                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_SPEC_NOT_FOUND', 404)
                if selection.allows_entry(identity):
                    profile = self._repository.load_profile(spec.strategy_ref, spec.forward_profile_id, **args)
                    self._repository.revalidate_stored_mock_automation_spec(spec, profile, **args)
                admission = self._repository.load_mock_automation_admission_for_spec(bundle.account_ref, identity, **args)
                retained = getattr(selection, 'retained_admission_ref', lambda _: None)(identity)
                original_standalone = (admission is not None and admission.version == 'mock_automation_admission/v1'
                    and retained is not None and retained.original_selection_id is None)
                if (admission is None or admission.version != MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION and not original_standalone
                        or (admission.account_ref, admission.spec_id, admission.execution_run_id)
                        != (bundle.account_ref, identity, bundle.run_id)
                        or not selection.matches_admission(spec_id=identity, admission_id=admission.admission_id,
                            original_selection_id=admission.account_selection_id, execution_run_id=admission.execution_run_id)):
                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_ADMISSION_MISSING', 404)
                receipt = self._repository.load_mock_automation_lease_for_admission(bundle.account_ref, admission.admission_id, **args)
                if (receipt is None or (receipt.admission_id, receipt.spec_id, receipt.account_ref, receipt.execution_run_id,
                        receipt.new_orders_enabled) != (admission.admission_id, identity, bundle.account_ref, bundle.run_id, False)):
                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_ADMISSION_MISSING', 404)
                admissions.append(admission)
        return tuple(admissions)

    async def _recover_account_selection(self, bundle, selection):
        from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_account_recovery_from_cut
        bundle.monitor.set_automation_execution_inputs_enabled(True, request_recovery=False)
        await bundle.monitor.refresh_recovery()
        cut = bundle.monitor.latest_automation_execution_cut
        if cut is None:
            raise MockAutomationSupervisorError('MOCK_AUTOMATION_ACCOUNT_RECOVERY_CUT_MISSING', 503)
        await drain_thread_call(record_mock_automation_account_recovery_from_cut,
            self._repository, bundle.runtime, cut, account_ref=bundle.account_ref, selection_id=selection.selection_id)
        return cut['recovery'], cut['risk']

    async def _recover_existing_control(self, bundle, control):
        if control.account_selection is not None:
            return await self._recover_account_selection(bundle, control.account_selection)
        await bundle.monitor.refresh_recovery()
        cut = bundle.monitor.latest_automation_execution_cut
        if cut is None:
            raise MockAutomationSupervisorError('MOCK_AUTOMATION_ACCOUNT_RECOVERY_CUT_MISSING', 503)
        await drain_thread_call(record_mock_automation_recovery_from_risk, self._repository, bundle.runtime,
            cut['recovery'], cut['risk'], account_ref=bundle.account_ref, spec_id=control.active_spec_id,
            observation=cut['observation'])
        return cut['recovery'], cut['risk']

    async def accept_selected_order_management(self, operation_id, *, credential_profile_id,
            credential_revision, account_ref, binding_revision, packet_sha256,
            expected_control_revision, expected_settings_revision, broker_evidence_provider=None):
        """Start one current original-plan manager after explicit native acceptance."""
        from kiwoom_monitor.domain.selected_order_evidence import COLLECTION, OWNER
        from .selected_order_bootstrap import INSTALLATIONS, ASSESSMENTS, CURRENT_MANAGEMENT
        lock = self._locks.setdefault(account_ref, asyncio.Lock())
        async with lock:
            preserved = await drain_thread_call(self._store.load_document, COLLECTION, OWNER, operation_id)
            if preserved is None or preserved['document']['receipt']['sha256'] != packet_sha256:
                raise MockAutomationSupervisorError('SELECTED_MANAGEMENT_INSTALLED_PACKET_CHANGED')
            packet = preserved['document']['packet']
            installed = await drain_thread_call(self._store.load_document, INSTALLATIONS,
                packet['account_ref'], packet['order']['intent_id'])
            if installed is None:
                raise MockAutomationSupervisorError('SELECTED_MANAGEMENT_INSTALLATION_REQUIRED')
            assessment = await drain_thread_call(self._store.load_document, ASSESSMENTS, OWNER,
                installed['document']['assessment_id'])
            if assessment is None:
                raise MockAutomationSupervisorError('SELECTED_MANAGEMENT_SOURCE_PROOF_MISSING')
            proof = assessment['document']['source_identity_proof']

            async def accept(identity, evidence, current_owner):
                bundle = self._owner.bundle(credential_profile_id)
                context = getattr(bundle, 'selected_management_context', None)
                if (self._closing or context is None or context.operation_id != operation_id
                        or context.packet_sha256 != packet_sha256 or not current_owner()):
                    raise MockAutomationSupervisorError('SELECTED_MANAGEMENT_RECONCILIATION_OWNER_REQUIRED')
                await self._owner.require_execution_settings(credential_profile_id,
                    expected_settings_revision=expected_settings_revision, credential_revision=credential_revision)
                repository = ForwardEvaluationRepository(self._store, selected_management_context=context)
                spec = await drain_thread_call(repository.load_mock_automation_spec, context.account_ref, context.spec_id)
                bundle.runtime.set_new_orders_enabled(False)
                bundle.monitor.set_automation_execution_inputs_enabled(True, request_recovery=False)
                recovery = await bundle.monitor.refresh_recovery()
                cut = bundle.monitor.latest_automation_execution_cut
                if cut is None:
                    raise MockAutomationSupervisorError('SELECTED_MANAGEMENT_CURRENT_RISK_REQUIRED')
                await drain_thread_call(record_mock_automation_recovery_from_risk, repository, bundle.runtime,
                    recovery, cut['risk'], account_ref=context.account_ref, spec_id=context.spec_id,
                    observation=cut['observation'])
                committed = None
                async def commit(_episode):
                    nonlocal committed
                    try:
                        committed = await drain_thread_call(self._store.accept_selected_order_management, context,
                            identity=identity, source_proof=proof, broker_evidence=evidence,
                            lease_owner=bundle.runtime.run_id + ':' + bundle.runtime.owner_token,
                            expected_control_revision=expected_control_revision, current_owner=current_owner,
                            expected_settings_revision=expected_settings_revision)
                    except BaseException:
                        row = await drain_thread_call(self._store.load_document, CURRENT_MANAGEMENT,
                            context.canonical_account_ref, context.canonical_account_ref)
                        if (row is None or row['document'].get('status') != 'MANAGEMENT_READY'
                                or row['document']['context'] != context.to_dict()
                                or row['document']['accepted_control_revision'] != expected_control_revision + 1
                                or not current_owner()):
                            raise
                        committed = {'receipt': row['document'], 'replayed': True}
                        await drain_thread_call(bundle.runtime.heartbeat)
                    control = await drain_thread_call(repository.load_mock_automation_control, context.account_ref)
                    bundle.refresh_automation_control(control)
                existing = self._runners.get(context.account_ref)
                if existing is not None and existing.episode.is_bound_to(bundle):
                    await commit(existing.episode)
                else:
                    if existing is not None:
                        await self._close_runner(existing)
                    await self._join_runner_closes(context.account_ref)
                    runner = await self._assemble_runner(bundle, spec, credential_profile_id, before_attach=commit)
                    self._runners[context.account_ref] = runner
                return {**committed, 'supervisor': self.status(context.account_ref,
                    credential_profile_id=credential_profile_id)}

            return await self._owner.verify_selected_order_identity(packet, packet_sha256, proof,
                credential_profile_id=credential_profile_id, credential_revision=credential_revision,
                account_ref=account_ref, binding_revision=binding_revision,
                broker_evidence_provider=broker_evidence_provider, on_verified=accept)

    async def _assemble_runner(self, bundle, spec, credential_profile_id, *, before_attach=None,
                               selection=None, member_admissions=()):
        from .mock_automation_episode import MockAutomationEpisode
        from .mock_automation_runner import MockAutomationRunner
        episode_options = dict(price_source=self._price_source, now_provider=self._now,
                               leader_context_source=self._leader_context_source)
        if selection is None:
            episodes = (MockAutomationEpisode(self._store, bundle, spec, **episode_options),)
            runner_options = {}
        else:
            admissions = {value.spec_id: value for value in member_admissions}
            if set(admissions) != set(selection.member_spec_ids) or len(admissions) != len(member_admissions):
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_RUNNER_MEMBERS_MISMATCH')
            def load_members():
                return tuple(self._repository.load_mock_automation_spec(bundle.account_ref, identity,
                    require_canonical_document=True) for identity in selection.member_spec_ids)
            specs = await drain_thread_call(load_members)
            if any(value is None for value in specs):
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_SPEC_NOT_FOUND', 404)
            episodes = tuple(MockAutomationEpisode(self._store, bundle, value,
                member_admission=admissions[value.spec_id], **episode_options) for value in specs)
            runner_options = dict(member_episodes=episodes[1:], execution_order=selection.execution_order_spec_ids,
                                  account_selection=selection)
        runner = MockAutomationRunner(episodes[0], monitor=getattr(bundle, "monitor", None),
                                      poll_seconds=self._poll_seconds, **runner_options)
        self._preparing_runners.add(runner)
        try:
            for episode in episodes:
                await drain_thread_call(episode.prepare_restore)
            if self._closing or self._owner.bundle(credential_profile_id) is not bundle:
                raise MockAutomationSupervisorError("PROFILE_RUNTIME_NOT_READY", 503)
            if before_attach is not None:
                await before_attach(episodes if selection is not None else runner.episode)
                if self._closing or self._owner.bundle(credential_profile_id) is not bundle:
                    raise MockAutomationSupervisorError("PROFILE_RUNTIME_NOT_READY", 503)
            runner.attach_inputs()
            await runner.start()
            if self._closing or self._owner.bundle(credential_profile_id) is not bundle:
                raise MockAutomationSupervisorError("PROFILE_RUNTIME_NOT_READY", 503)
            return runner
        except BaseException:
            await self._close_runner(runner)
            raise
        finally:
            self._preparing_runners.discard(runner)

    def status(self, account_ref: str, *, credential_profile_id: str = "") -> dict[str, Any]:
        control = self._repository.load_mock_automation_control(account_ref)
        return self._status_from_control(
            account_ref, credential_profile_id=credential_profile_id, control=control,
        )

    def _status_from_control(self, account_ref: str, *, credential_profile_id: str, control) -> dict[str, Any]:
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
            bundle=self._require_automatic_bundle(document['credential_profile_id'],account_ref,document['spec_id'], allow_member=True)
            if bundle.automation_context.control_revision!=document['expected_control_revision']:
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT')
            runner=self._runners.get(account_ref)
            if runner is None or bundle.run_id!=document['execution_run_id']:
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_RUNTIME_NOT_ACTIVE')
            episode = runner.episode_for_spec(document['spec_id'])
            if episode is None:
                raise MockAutomationSupervisorError('MOCK_AUTOMATION_RUNTIME_NOT_ACTIVE')
            prior=episode.status.get('unfilled_population_source',{})
            if prior.get('publication_ref')!=document['publication_ref']:
                control=await drain_thread_call(self._repository.load_mock_automation_control,account_ref)
                if (control is None or control.desired_state is not MockAutomationDesiredState.RUNNING
                        or not control.includes_spec(document['spec_id'])
                        or (control.execution_run_id,control.control_revision)!=(
                            document['execution_run_id'],document['expected_control_revision'])):
                    raise MockAutomationSupervisorError('MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT')
            return await drain_thread_call(episode.offer_native_unfilled_publication,document)

    def _require_automatic_bundle(
        self, credential_profile_id: str, account_ref: str, spec_id: str, *, allow_member=False,
    ) -> Any:
        bundle = self._owner.bundle(credential_profile_id)
        if bundle is None or bundle.account_ref != account_ref:
            raise MockAutomationSupervisorError("PROFILE_RUNTIME_NOT_READY", 503)
        context = bundle.automation_context
        if (
            bundle.runtime_mode is not MockAccountRuntimeMode.AUTOMATIC
            or context is None
            or (context.spec_id != spec_id and not (
                allow_member and (runner := self._runners.get(account_ref)) is not None
                and getattr(runner, 'account_selection_id', None) is not None
                and runner.episode_for_spec(spec_id) is not None))
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
            runner = self._runners.get(account_ref)
            if runner is not None and runner.episode.is_bound_to(self._owner.bundle(binding.credential_profile_id)):
                continue
            if time.monotonic() < self._restore_retry_after.get(account_ref, 0.0):
                continue
            control = await asyncio.to_thread(
                self._repository.load_mock_automation_control, account_ref,
            )
            if control is None or (control.desired_state is not MockAutomationDesiredState.RUNNING
                    and not (control.desired_state is MockAutomationDesiredState.STOPPED
                             and getattr(control, 'account_selection', None) is not None)):
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
                    **({'account_selection_id': control.account_selection.selection_id}
                       if getattr(control, 'account_selection', None) is not None else {}),
                    **({'_restore_stopped_management': True}
                       if control.desired_state is MockAutomationDesiredState.STOPPED else {}),
                )
            except Exception as error:
                # activate may already have replaced its MANUAL bundle. Closing
                # that retired runtime would mask the actual admission error.
                current = self._owner.bundle(profile_id)
                if current is not None and current.account_ref == account_ref:
                    current.runtime.set_new_orders_enabled(False)
                detail = str(error)
                if self._restore_errors.get(account_ref) != detail:
                    logger.error(
                        "automatic mock runtime restore remains fail-closed account=%s error=%s",
                        account_ref, type(error).__name__,
                    )
                self._restore_errors[account_ref] = detail
                missing_adapter = strategy_implementation_unavailable(error)
                # A removed implementation needs explicit activation or restart,
                # while unrelated failures retain the existing timed recovery.
                self._restore_retry_after[account_ref] = float('inf') if missing_adapter else time.monotonic() + 30.0
