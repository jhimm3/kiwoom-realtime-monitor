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
                 poll_seconds: float = 1.0) -> None:
        self._episode = episode
        self._monitor = monitor
        self._poll_seconds = max(0.25, float(poll_seconds))
        self._task: asyncio.Task[None] | None = None
        self._wake_event: asyncio.Event | None = None
        self._wake_loop: asyncio.AbstractEventLoop | None = None
        self._native_risk_listener = None
        self._inputs_attached = False
        self._execution_inputs_attached = False
        episode.bind_execution_callbacks(
            scheduled_task_present=self._has_scheduled_task, capture_wake=self._capture_wake,
        )

    @property
    def episode(self) -> MockAutomationEpisode:
        return self._episode

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
        if self._episode.closing:
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_CLOSED")
        if not self._episode.prepared:
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_NOT_PREPARED")
        if self._inputs_attached:
            return
        monitor = self._monitor
        setter = getattr(monitor, "set_automation_risk_listener", None)
        try:
            if callable(setter):
                self._native_risk_listener = self._episode.observe_native_risk
                setter(self._native_risk_listener)
            source_setter = getattr(monitor, 'set_automation_execution_inputs_enabled', None)
            if self._episode.needs_native_execution_inputs and callable(source_setter):
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
        if self._episode.closing:
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_CLOSED")
        if not self._episode.prepared or not self._inputs_attached:
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_NOT_PREPARED")
        if self._episode.execution_busy:
            raise RuntimeError("unfilled_preflight_already_in_progress")
        if self._task is None or self._task.done():
            self._wake_loop = asyncio.get_running_loop()
            self._wake_event = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="mock-automation-runner")

    async def close(self) -> None:
        self._episode.begin_close(retire_wake=self._retire_wake)
        self._detach_inputs()
        if self._task is None:
            await self._episode.finish_close(scheduled_task_was_present=False)
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        await self._episode.finish_close(scheduled_task_was_present=True)

    async def _run(self) -> None:
        if self._wake_event is None:
            self._wake_loop = asyncio.get_running_loop()
            self._wake_event = asyncio.Event()
        while True:
            self._wake_event.clear()
            try:
                await self._episode.advance_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("automatic mock runner iteration failed")
                await self._episode.record_iteration_error(error)
            try:
                timeout = self._episode.next_poll_delay(self._poll_seconds)
                await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

