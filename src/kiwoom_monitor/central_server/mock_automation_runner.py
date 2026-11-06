"""Own attachment, the main task and scheduling for one mock run Episode."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .mock_automation_episode import MockAutomationEpisode


logger = logging.getLogger(__name__)


class MockAutomationRunner:
    """Own concrete execution lifetime; business consumers use the Episode."""

    def __init__(self, episode: MockAutomationEpisode, *, monitor: Any = None,
                 poll_seconds: float = 1.0, member_episodes=(), execution_order=None) -> None:
        self._episode = episode
        self._episodes = (episode, *member_episodes)
        self._execution_episodes = self._episodes
        if len(self._episodes) > 1:
            if (len({value.spec_id for value in self._episodes}) != len(self._episodes)
                    or any(not value.shares_account_membership(episode) for value in self._episodes)):
                raise ValueError('MOCK_AUTOMATION_RUNNER_MEMBERS_MISMATCH')
            # The anchor remains the account's compatibility handle. It does
            # not establish trading priority; that order must be explicit.
            if (execution_order is None or len(execution_order) != len(self._episodes)
                    or set(execution_order) != {value.spec_id for value in self._episodes}):
                raise ValueError('MOCK_AUTOMATION_EXPLICIT_MEMBER_EXECUTION_ORDER_REQUIRED')
            members = {value.spec_id: value for value in self._episodes}
            self._execution_episodes = tuple(members[identity] for identity in execution_order)
        self._monitor = monitor
        self._poll_seconds = max(0.25, float(poll_seconds))
        self._task: asyncio.Task[None] | None = None
        self._wake_event: asyncio.Event | None = None
        self._wake_loop: asyncio.AbstractEventLoop | None = None
        self._native_risk_listener = None
        self._inputs_attached = False
        self._execution_inputs_attached = False
        for value in self._episodes:
            value.bind_execution_callbacks(
                scheduled_task_present=self._has_scheduled_task, capture_wake=self._capture_wake,
            )

    @property
    def episode(self) -> MockAutomationEpisode:
        return self._episode

    @property
    def episodes(self) -> tuple[MockAutomationEpisode, ...]:
        return self._episodes

    def episode_for_spec(self, spec_id: str) -> MockAutomationEpisode | None:
        return next((value for value in self._episodes if value.spec_id == spec_id), None)

    def _observe_native_risk(self, cut):
        first_error = None
        for value in self._episodes:
            try:
                value.observe_native_risk(cut)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _has_scheduled_task(self) -> bool:
        return self._task is not None

    def _retire_wake(self) -> None:
        self._wake_loop = None

    def _capture_wake(self, *, tolerate_retired_loop=False):
        loop, event = self._wake_loop, self._wake_event
        if loop is None or event is None:
            return None
        if tolerate_retired_loop:
            def notify():
                if not loop.is_closed():
                    try:
                        loop.call_soon_threadsafe(event.set)
                    except RuntimeError:
                        pass  # Close retired the loop; the cut cannot authorize work.
        else:
            def notify():
                loop.call_soon_threadsafe(event.set)
        return notify

    def attach_inputs(self) -> None:
        """Attach the prepared episode to its monitor on the owning loop."""
        if any(value.closing for value in self._episodes):
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_CLOSED")
        if not all(value.prepared for value in self._episodes):
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_NOT_PREPARED")
        if any(value.admitted_member_spec_ids and
                set(value.admitted_member_spec_ids) != {member.spec_id for member in self._episodes}
                for value in self._episodes):
            raise ValueError('MOCK_AUTOMATION_RUNNER_MEMBERS_MISMATCH')
        if self._inputs_attached:
            return
        monitor = self._monitor
        setter = getattr(monitor, "set_automation_risk_listener", None)
        try:
            if callable(setter):
                self._native_risk_listener = (self._episode.observe_native_risk if len(self._episodes) == 1
                                              else self._observe_native_risk)
                setter(self._native_risk_listener)
            source_setter = getattr(monitor, 'set_automation_execution_inputs_enabled', None)
            if any(value.needs_native_execution_inputs for value in self._episodes) and callable(source_setter):
                self._execution_inputs_attached = True
                source_setter(True)
            self._inputs_attached = True
        except BaseException:
            try:
                self._detach_inputs()
            except BaseException:
                logger.exception("automatic mock runner input rollback failed")
            raise

    def _detach_inputs(self) -> None:
        monitor = self._monitor
        try:
            if self._execution_inputs_attached:
                monitor.set_automation_execution_inputs_enabled(False)
                self._execution_inputs_attached = False
        finally:
            if self._native_risk_listener is not None:
                monitor.clear_automation_risk_listener(self._native_risk_listener)
                self._native_risk_listener = None
            self._inputs_attached = False

    async def start(self) -> None:
        if any(value.closing for value in self._episodes):
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_CLOSED")
        if not all(value.prepared for value in self._episodes) or not self._inputs_attached:
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_NOT_PREPARED")
        if any(value.execution_busy for value in self._episodes):
            raise RuntimeError("unfilled_preflight_already_in_progress")
        if self._task is None or self._task.done():
            self._wake_loop = asyncio.get_running_loop()
            self._wake_event = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="mock-automation-runner")

    async def close(self) -> None:
        for value in self._episodes:
            value.begin_close(retire_wake=self._retire_wake)
        self._detach_inputs()
        if self._task is None:
            await self._finish_episodes(scheduled_task_was_present=False)
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        await self._finish_episodes(scheduled_task_was_present=True)

    async def _finish_episodes(self, *, scheduled_task_was_present):
        first_error = None
        for value in self._episodes:
            try:
                await value.finish_close(scheduled_task_was_present=scheduled_task_was_present)
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    async def _run(self) -> None:
        if self._wake_event is None:
            self._wake_loop = asyncio.get_running_loop()
            self._wake_event = asyncio.Event()
        while True:
            self._wake_event.clear()
            for value in self._execution_episodes:
                try:
                    await value.advance_once()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.exception("automatic mock runner iteration failed")
                    await value.record_iteration_error(error)
            try:
                timeout = min(value.next_poll_delay(self._poll_seconds) for value in self._episodes)
                await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

