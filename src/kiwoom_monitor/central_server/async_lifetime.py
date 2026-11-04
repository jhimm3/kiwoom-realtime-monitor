"""Normal task/thread calls with explicit, scoped ownership supplied by callers."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar


_WORK_OWNER = ContextVar("async_work_owner", default=None)
_TASK_CONTEXT = ContextVar("async_task_context", default=None)


@contextmanager
def bind_owned_work(*, create_task, to_thread):
    """Bind an existing scope's submission functions; nested scopes reset exactly."""
    token = _WORK_OWNER.set((create_task, to_thread))
    try:
        yield
    finally:
        _WORK_OWNER.reset(token)


@contextmanager
def bind_task_context(context_factory):
    """Let an opt-in caller assign child context at submission, before task spawn."""
    token = _TASK_CONTEXT.set(context_factory)
    try:
        yield
    finally:
        _TASK_CONTEXT.reset(token)


def owned_create_task(coroutine, *, name=None, shutdown=False):
    context_factory = _TASK_CONTEXT.get()
    with context_factory(name) if context_factory is not None else nullcontext():
        owner = _WORK_OWNER.get()
        if owner is None:
            return asyncio.create_task(coroutine, name=name)
        return owner[0](coroutine, name=name, shutdown=shutdown)


async def owned_to_thread(function, /, *args, **kwargs):
    owner = _WORK_OWNER.get()
    if owner is None:
        return await asyncio.to_thread(function, *args, **kwargs)
    return await owner[1](function, *args, **kwargs)


async def await_native_call(function, /, *args, **kwargs):
    """Join one accepted native call before propagating request cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    return await await_owned_task(task)


async def await_owned_task(task):
    """Join an existing caller-owned task, preserving its result or native cause."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            # Retrieve the completed native failure below, retaining
            # it as the cause if the request was already cancelled.
            break
    try:
        result = task.result()
    except Exception as error:
        if cancelled:
            raise asyncio.CancelledError from error
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result
