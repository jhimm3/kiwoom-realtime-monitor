"""Product operation correlation shared by synchronous and asynchronous callers.

These values identify an operation for observation. They grant no account,
request, native transaction, or execution authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


_DB_CALL_SOURCE: ContextVar[str] = ContextVar("db_call_source", default="")
_DB_CALL_REQUEST_ID: ContextVar[str] = ContextVar("db_call_request_id", default="")


def current_db_call_tags() -> dict[str, str]:
    return {"source": _DB_CALL_SOURCE.get()[:100], "request_id": _DB_CALL_REQUEST_ID.get()[:100]}


@contextmanager
def db_call_source(source: str):
    """Identify the owning operation across asyncio.to_thread DB reads."""
    token = _DB_CALL_SOURCE.set(source)
    try:
        yield
    finally:
        _DB_CALL_SOURCE.reset(token)


@contextmanager
def db_call_request_id(request_id: str):
    """Attach a bounded external operation ID to calls made in this scope."""
    token = _DB_CALL_REQUEST_ID.set(request_id[:100])
    try:
        yield
    finally:
        _DB_CALL_REQUEST_ID.reset(token)


@dataclass(frozen=True)
class OperationContextSnapshot:
    source: str = ""
    request_id: str = ""


def get_operation_source() -> str:
    return _DB_CALL_SOURCE.get()


def get_operation_request_id() -> str:
    return _DB_CALL_REQUEST_ID.get()


def snapshot_operation_context() -> OperationContextSnapshot:
    return OperationContextSnapshot(
        source=get_operation_source(), request_id=get_operation_request_id(),
    )


@contextmanager
def operation_context_scope(snapshot: OperationContextSnapshot) -> Iterator[None]:
    """Apply a value snapshot to the same state and restore both scope tokens."""
    with db_call_source(snapshot.source), db_call_request_id(snapshot.request_id):
        yield


def validated_request_id(value: object) -> str:
    """Read safe correlation metadata without accepting or rejecting business work."""
    if not isinstance(value, str) or not value or len(value) > 100:
        return ""
    if any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value):
        return ""
    return value
