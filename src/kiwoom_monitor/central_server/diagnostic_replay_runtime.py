"""Opt-in ownership of native replay tasks and actual executor work.

No global task factory/executor patch: native call sites explicitly use the two
helpers below. Outside an activated replay scope they retain asyncio behavior.
This module does not start a TOP20 runner or authorize network/DB execution.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import math
from threading import RLock, get_ident


_RUNTIME = ContextVar("owned_replay_runtime", default=None)
_SHUTDOWN = ContextVar("owned_replay_shutdown", default=False)
_THREAD = ContextVar("owned_replay_thread", default=None)


def owned_create_task(coroutine, *, name=None, shutdown=False):
    from .diagnostic_rest_input import replay_task_input_context
    with replay_task_input_context(name):
        runtime = _RUNTIME.get()
        if runtime is None:
            return asyncio.create_task(coroutine, name=name)
        return runtime.create_task(coroutine, name=name, shutdown=shutdown)


async def owned_to_thread(function, /, *args, **kwargs):
    runtime = _RUNTIME.get()
    if runtime is None:
        return await asyncio.to_thread(function, *args, **kwargs)
    return await runtime.to_thread(function, *args, **kwargs)


class ReplayRuntimeScope:
    """One loop/run owns submissions before an executor thread opens a DB.

    Cancellation of a waiter never retires executor work. Completion is marked
    in the actual thread's finally block, not by a cancelled asyncio Future.
    A timeout quarantines the run; an explicit later drain can prove it idle,
    but the timeout/error receipt still prevents performance acceptance.
    """

    def __init__(self, *, capacity=4096):
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("replay_runtime_capacity_invalid")
        self._capacity = capacity
        self._lock = RLock()
        self._loop = None
        self._tasks = set()
        self._threads = {}
        self._sequence = 0
        self._phase = "running"
        self._errors = []
        self._error_count = 0
        self._timed_out = False
        self._drain_task = None
        self._closing = False

    @contextmanager
    def activate(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if loop is not self._loop:
            raise RuntimeError("replay_runtime_loop_changed")
        current = _RUNTIME.get()
        if current is not None and current is not self:
            raise RuntimeError("replay_runtime_shared_owner")
        token = _RUNTIME.set(self)
        try:
            yield self
        finally:
            _RUNTIME.reset(token)

    def _failure(self, kind, error_type):
        with self._lock:
            self._error_count += 1
            if len(self._errors) < 256:
                self._errors.append({"kind": kind, "error_type": error_type})

    def _admit(self, *, shutdown):
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("replay_runtime_not_activated")
        with self._lock:
            cleanup_after_timeout = self._phase == "quarantined" and self._closing and shutdown
            if (self._phase not in {"running", "shutdown"} and not cleanup_after_timeout
                    or self._phase == "shutdown" and not shutdown):
                self._failure("late_submission", "RuntimeError")
                raise RuntimeError("replay_runtime_new_work_fenced")
            if len(self._tasks) + len(self._threads) >= self._capacity:
                self._failure("capacity", "RuntimeError")
                raise RuntimeError("replay_runtime_capacity_exceeded")

    def create_task(self, coroutine, *, name=None, shutdown=False):
        cleanup = shutdown or _SHUTDOWN.get()
        try:
            self._admit(shutdown=cleanup)
        except BaseException:
            coroutine.close()
            raise
        # A shutdown owner admits its final flush children, while a cancelled
        # producer cannot restart a fresh preparation/subscription task.
        token = _SHUTDOWN.set(cleanup)
        try:
            task = asyncio.create_task(coroutine, name=name)
        finally:
            _SHUTDOWN.reset(token)
        with self._lock:
            self._tasks.add(task)

        def finished(done):
            if not done.cancelled():
                error = done.exception()
                if error is not None:
                    self._failure("task", type(error).__name__)
            with self._lock:
                self._tasks.discard(done)
        task.add_done_callback(finished)
        return task

    async def to_thread(self, function, /, *args, **kwargs):
        # Existing broker/persistence owners must finish their admitted requests
        # while service producers stop. Only fresh producers are fenced here.
        self._admit(shutdown=_SHUTDOWN.get() or asyncio.current_task() in self._tasks)
        context = copy_context()
        with self._lock:
            self._sequence += 1
            identifier = self._sequence
            self._threads[identifier] = {"state": "submitted", "thread_id": None}

        def invoke():
            with self._lock:
                self._threads[identifier] = {"state": "running", "thread_id": get_ident()}
            try:
                def call():
                    token = _THREAD.set((self, identifier))
                    try:
                        return function(*args, **kwargs)
                    finally:
                        _THREAD.reset(token)
                return context.run(call)
            except BaseException as error:
                self._failure("thread", type(error).__name__)
                raise
            finally:
                with self._lock:
                    self._threads.pop(identifier, None)
        try:
            future = self._loop.run_in_executor(None, invoke)
        except BaseException as error:
            with self._lock:
                self._threads.pop(identifier, None)
            self._failure("submit", type(error).__name__)
            raise
        future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(future)

    def begin_shutdown(self):
        with self._lock:
            if self._phase == "running":
                self._phase = "shutdown"
            elif self._phase != "shutdown":
                raise RuntimeError("replay_runtime_shutdown_already_sealed")

    async def drain(self, *, timeout=60.0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("replay_runtime_drain_timeout_invalid")
        with self._lock:
            if self._closing:
                raise RuntimeError("replay_runtime_not_drained")
            if asyncio.current_task() in self._tasks:
                raise RuntimeError("replay_runtime_drain_cannot_own_itself")
            if self._phase == "running":
                raise RuntimeError("replay_runtime_producers_not_stopped")
            if self._phase != "drained":
                self._phase = "sealed"
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain(float(timeout)), name="replay-runtime-drain")
            self._drain_task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        # The drain owner continues if a UI/timeout waiter is cancelled.
        return await asyncio.shield(self._drain_task)

    async def _drain(self, timeout):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            with self._lock:
                if not self._tasks and not self._threads:
                    self._phase = "drained"
                    return self.status()
            if asyncio.get_running_loop().time() >= deadline:
                with self._lock:
                    self._timed_out = True
                    self._phase = "quarantined"
                raise RuntimeError("replay_runtime_not_drained")
            await asyncio.sleep(.005)

    def require_drained(self):
        with self._lock:
            if self._phase != "drained" or self._tasks or self._threads:
                raise RuntimeError("replay_runtime_not_drained")

    def require_active(self):
        if _RUNTIME.get() is not self or asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("replay_runtime_not_activated")
        with self._lock:
            if self._phase != "running":
                raise RuntimeError("replay_runtime_new_work_fenced")

    def require_native_work(self):
        owner = _THREAD.get()
        with self._lock:
            if (_RUNTIME.get() is not self or owner is None or owner[0] is not self
                    or self._threads.get(owner[1], {}).get("thread_id") != get_ident()):
                raise RuntimeError("replay_runtime_unowned_native_connection")

    def status(self):
        with self._lock:
            return {"phase": self._phase, "pending_tasks": len(self._tasks),
                    "pending_threads": len(self._threads),
                    "submitted_threads": sum(value["state"] == "submitted" for value in self._threads.values()),
                    "errors": list(self._errors), "error_count": self._error_count,
                    "errors_truncated": self._error_count > len(self._errors),
                    "timed_out": self._timed_out,
                    "execution_succeeded": self._phase == "drained" and not self._error_count and not self._timed_out}


async def close_top20_replay_resources(runtime, service, broker, *, collector=None, timeout=60.0):
    """Native stop/flush order, followed by proof of every actual thread exit.

    The caller owns DB/file restore. This helper cannot restore on a timeout and
    does not treat swallowed native write failures as successful execution.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("replay_runtime_drain_timeout_invalid")
    runtime.begin_shutdown()

    async def close_resources():
        try:
            for resource in (service, collector, broker):
                if resource is not None:
                    try:
                        await resource.close()
                    except Exception as error:
                        runtime._failure("close", type(error).__name__)
        finally:
            runtime._closing = False
    with runtime.activate():
        closing = owned_create_task(close_resources(), name="top20-replay-close", shutdown=True)
        runtime._closing = True
    deadline = asyncio.get_running_loop().time() + timeout
    cancelled = False
    while not closing.done():
        try:
            await asyncio.wait_for(asyncio.shield(closing), max(.001, deadline - asyncio.get_running_loop().time()))
        except asyncio.CancelledError:
            if not cancelled:
                runtime._failure("close_waiter_cancelled", "CancelledError")
            cancelled = True
        except TimeoutError:
            runtime._failure("close_timeout", "TimeoutError")
            with runtime._lock:
                runtime._timed_out = True
                runtime._phase = "quarantined"
            raise RuntimeError("replay_runtime_not_drained") from None
    closing.result()
    while True:
        try:
            result = await runtime.drain(timeout=timeout)
            break
        except asyncio.CancelledError:
            if not cancelled:
                runtime._failure("close_waiter_cancelled", "CancelledError")
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result
