"""Opt-in PostgreSQL call observation without changing connection ownership.

The query cache caller uses explicit commit and close; news job completion uses
the native connection context. A caller-owned transaction can be observed
without taking ownership of its connection or nested savepoints.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic, time
from typing import Any, Callable, Protocol
from uuid import uuid4


_DB_CALL_SOURCE: ContextVar[str] = ContextVar("db_call_source", default="")


@contextmanager
def db_call_source(source: str):
    """Identify the owning operation across asyncio.to_thread DB reads."""
    token = _DB_CALL_SOURCE.set(source)
    try:
        yield
    finally:
        _DB_CALL_SOURCE.reset(token)


@dataclass(frozen=True)
class DBWriterContext:
    writer_family: str
    writer_kind: str
    operation: str
    rows_attempted: int | None = None
    api_id: str = ""
    source: str = field(default_factory=_DB_CALL_SOURCE.get)
    parent_call_id: str = ""
    request_id: str = ""
    call_id: str = field(default_factory=lambda: uuid4().hex)
    access_mode: str = "write"


class DBSlowHook(Protocol):
    """A bounded observer can watch a running stage on its own connection."""

    def stage_started(self, call_id: str, stage: str, at: float, backend_pid: int | None) -> None: ...
    def stage_finished(self, call_id: str, stage: str, at: float, duration_ms: float) -> None: ...
    def call_finished(self, record: dict[str, object]) -> None: ...


def _milliseconds(started: float) -> float:
    return round((monotonic() - started) * 1000, 3)


class ObservedDBCursor:
    def __init__(self, raw: Any, owner: ObservedDBConnection) -> None:
        self._raw = raw
        self._owner = owner

    def __enter__(self) -> ObservedDBCursor:
        try:
            self._raw.__enter__()
        except BaseException as error:
            self._owner._failure("cursor_enter", error)
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        try:
            return self._raw.__exit__(exc_type, exc, traceback)
        except BaseException as error:
            self._owner._failure("cursor_exit", error)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._run("execute", self._raw.execute, args, kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> Any:
        return self._run("executemany", self._raw.executemany, args, kwargs)

    def _run(self, method: str, operation: Callable[..., Any],
             args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if self._owner._transaction_started is False:
            self._owner._transaction_started = None
        self._owner._notify_started(method)
        started_at = time()
        started = monotonic()
        try:
            result = operation(*args, **kwargs)
            self._owner._transaction_started = True
            return self if result is self._raw else result
        except BaseException as error:
            self._owner._failure(method, error)
            raise
        finally:
            duration = _milliseconds(started)
            self._owner._execute_ms += duration
            self._owner._sql_calls += 1
            if len(self._owner._execute_windows) < 16:
                self._owner._execute_windows.append({
                    "method": method, "started_at": started_at,
                    "finished_at": time(), "duration_ms": duration,
                })
            else:
                self._owner._execute_windows_truncated = True
            self._owner._notify_finished(method, duration)


class ObservedDBConnection:
    """Proxy for one measured call; native connection ownership stays with its caller."""

    def __init__(self, raw: Any, context: DBWriterContext | None,
                 *, started_at: float, started_mono: float,
                 connect_ms: float | None, capture_token: tuple[bool, str | None],
                 hook: DBSlowHook | None = None) -> None:
        self._raw = raw
        self._context = context
        self._started_at = started_at
        self._started_mono = started_mono
        self._connect_ms = connect_ms
        self._capture_token = capture_token
        self._hook = hook
        # None means the first statement failed before a transaction was confirmed.
        self._transaction_started: bool | None = False
        self._sql_calls = 0
        self._execute_ms = 0.0
        self._execute_windows: list[dict[str, object]] = []
        self._execute_windows_truncated = False
        self._commit_ms: float | None = None
        self._commit_started_at: float | None = None
        self._commit_finished_at: float | None = None
        self._rollback_ms: float | None = None
        self._rollback_started_at: float | None = None
        self._rollback_finished_at: float | None = None
        self._close_ms: float | None = None
        self._commits = 0
        self._rollbacks = 0
        self._outcome = "not_started"
        self._errors: list[dict[str, str]] = []
        self._body_exception_type: str | None = None
        self._finished = False
        self.call_id = context.call_id if context else uuid4().hex
        pid = getattr(getattr(raw, "info", None), "backend_pid", None)
        self.backend_pid = int(pid) if pid else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    def __enter__(self) -> ObservedDBConnection:
        self._raw.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        # Psycopg's Python __exit__ invokes self.commit()/rollback()/close().
        # Run that same method with this proxy as self so those calls retain
        # the driver's order and exception behavior while being measured here.
        if exc is not None:
            self._body_exception_type = type(exc).__name__
        try:
            return type(self._raw).__exit__(self, exc_type, exc, traceback)
        finally:
            # Psycopg skips close if commit raises (or when pool-owned).
            if not self._finished:
                self._finished = True
                self._publish()

    def cursor(self, *args: Any, **kwargs: Any) -> ObservedDBCursor:
        try:
            return ObservedDBCursor(self._raw.cursor(*args, **kwargs), self)
        except BaseException as error:
            self._failure("cursor", error)
            raise

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self.cursor().execute(*args, **kwargs)

    def commit(self) -> None:
        self._notify_started("commit")
        self._commit_started_at = time()
        started = monotonic()
        try:
            self._raw.commit()
            self._commits += 1
            self._outcome = "committed"
        except BaseException as error:
            # A lost acknowledgement does not prove whether PostgreSQL committed.
            self._outcome = "unknown"
            self._failure("commit", error)
            raise
        finally:
            self._commit_finished_at = time()
            self._commit_ms = _milliseconds(started)
            self._notify_finished("commit", self._commit_ms)

    def rollback(self) -> None:
        self._notify_started("rollback")
        self._rollback_started_at = time()
        started = monotonic()
        try:
            self._raw.rollback()
            self._rollbacks += 1
            if self._outcome != "unknown":
                self._outcome = "rolled_back"
        except BaseException as error:
            self._outcome = "unknown"
            self._failure("rollback", error)
            raise
        finally:
            self._rollback_finished_at = time()
            self._rollback_ms = _milliseconds(started)
            self._notify_finished("rollback", self._rollback_ms)

    def close(self) -> None:
        started = monotonic()
        try:
            self._raw.close()
            if self._outcome == "not_started":
                if self._transaction_started is True:
                    self._outcome = "closed_uncommitted"
                elif self._transaction_started is None:
                    self._outcome = "closed_transaction_unknown"
        except BaseException as error:
            self._outcome = "unknown"
            self._failure("close", error)
            raise
        finally:
            self._close_ms = _milliseconds(started)
            if not self._finished:
                self._finished = True
                self._publish()

    def _failure(self, stage: str, error: BaseException) -> None:
        if len(self._errors) < 8:
            self._errors.append({"stage": stage, "exception_type": type(error).__name__})

    def _hook_active(self) -> bool:
        if self._hook is None or not self._capture_token[0]:
            return False
        try:
            from .diagnostic_metrics import capture_session_token, refresh_capture_state

            refresh_capture_state(force=True)
            return capture_session_token() == self._capture_token
        except Exception as error:
            self._failure("observer", error)
            return False

    def _notify_started(self, stage: str) -> None:
        if self._hook_active():
            try:
                self._hook.stage_started(self.call_id, stage, time(), self.backend_pid)
            except Exception as error:
                self._failure("observer", error)

    def _notify_finished(self, stage: str, duration_ms: float) -> None:
        if self._hook_active():
            try:
                self._hook.stage_finished(self.call_id, stage, time(), duration_ms)
            except Exception as error:
                self._failure("observer", error)

    def _publish(self) -> None:
        if not self._capture_token[0]:
            return
        if self._body_exception_type is not None and not self._errors:
            self._errors.append({"stage": "body", "exception_type": self._body_exception_type})
        context = self._context
        record: dict[str, object] = {
            "at": time(), "started_at": self._started_at,
            "finished_at": time(), "call_id": self.call_id,
            "writer_family": context.writer_family if context else "UNREGISTERED",
            "writer_kind": context.writer_kind if context else "UNREGISTERED",
            "access_mode": context.access_mode if context else "write",
            "operation": context.operation if context else "unregistered",
            "api_id": context.api_id if context else "",
            "source": context.source if context else "",
            "parent_call_id": context.parent_call_id if context else "",
            "request_id": context.request_id if context else "",
            "backend_pid": self.backend_pid,
            "rows_attempted": context.rows_attempted if context else None,
            "calls": 1, "transactions": (None if self._transaction_started is None
                                            else int(self._transaction_started)),
            "commits": self._commits, "rollbacks": self._rollbacks,
            "sql_calls": self._sql_calls,
            "execute_windows": list(self._execute_windows),
            "execute_windows_truncated": self._execute_windows_truncated,
            "connection_acquire_ms": self._connect_ms,
            "execute_ms": round(self._execute_ms, 3),
            "commit_ms": self._commit_ms, "rollback_ms": self._rollback_ms,
            "commit_started_at": self._commit_started_at,
            "commit_finished_at": self._commit_finished_at,
            "rollback_started_at": self._rollback_started_at,
            "rollback_finished_at": self._rollback_finished_at,
            "close_ms": self._close_ms,
            "total_ms": _milliseconds(self._started_mono),
            "outcome": self._outcome, "errors": list(self._errors),
            "retries": None,
        }
        try:
            from .diagnostic_metrics import record_db_call

            record_db_call(record, capture_token=self._capture_token)
            if self._hook_active():
                self._hook.call_finished(record)
        except Exception:
            # Observability cannot change whether an already finished write succeeds.
            pass


class ObservedExistingDBTransaction:
    """Observe one native transaction on a caller-owned connection."""

    def __init__(self, raw: Any, context: DBWriterContext,
                 *, hook: DBSlowHook | None = None) -> None:
        from .diagnostic_metrics import capture_session_token

        try:
            capture_token = capture_session_token()
        except Exception:
            capture_token = (False, None)
        self.connection = ObservedDBConnection(
            raw, context, started_at=time(), started_mono=monotonic(),
            connect_ms=None, capture_token=capture_token, hook=hook,
        )
        self._native_transaction: Any = None

    def __enter__(self) -> ObservedDBConnection:
        try:
            self._native_transaction = self.connection._raw.transaction()
            self._native_transaction.__enter__()
            self.connection._transaction_started = True
            return self.connection
        except BaseException as error:
            self.connection._outcome = "begin_error"
            self.connection._failure("begin", error)
            self.connection._finished = True
            self.connection._publish()
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        observed = self.connection
        stage = "rollback" if exc is not None else "commit"
        if exc is not None:
            observed._body_exception_type = type(exc).__name__
        observed._notify_started(stage)
        started = monotonic()
        if stage == "commit":
            observed._commit_started_at = time()
        else:
            observed._rollback_started_at = time()
        try:
            result = self._native_transaction.__exit__(exc_type, exc, traceback)
            if stage == "commit":
                observed._commits += 1
                observed._outcome = "committed"
            else:
                observed._rollbacks += 1
                observed._outcome = "rolled_back"
            return result
        except BaseException as error:
            observed._outcome = "unknown"
            observed._failure(stage, error)
            raise
        finally:
            duration = _milliseconds(started)
            if stage == "commit":
                observed._commit_finished_at = time()
                observed._commit_ms = duration
            else:
                observed._rollback_finished_at = time()
                observed._rollback_ms = duration
            observed._notify_finished(stage, duration)
            observed._finished = True
            observed._publish()


def observe_existing_transaction(connection: Any, context: DBWriterContext, *,
                                 hook: DBSlowHook | None = None
                                 ) -> ObservedExistingDBTransaction:
    """Measure an outer transaction; leave connection lifetime and savepoints to its caller."""
    return ObservedExistingDBTransaction(connection, context, hook=hook)


def open_observed_connection(connect: Callable[[], Any],
                             context: DBWriterContext | None, *,
                             hook: DBSlowHook | None = None) -> ObservedDBConnection:
    """Call the existing factory exactly once and preserve its failure behavior."""
    from .diagnostic_metrics import capture_session_token, record_db_call

    try:
        capture_token = capture_session_token()
    except Exception:
        # A broken diagnostic control must not prevent the actual DB call.
        capture_token = (False, None)
    started_at, started_mono = time(), monotonic()
    try:
        raw = connect()
    except BaseException as error:
        if capture_token[0]:
            record: dict[str, object] = {
                "at": time(), "started_at": started_at, "finished_at": time(),
                "call_id": context.call_id if context else uuid4().hex,
                "writer_family": context.writer_family if context else "UNREGISTERED",
                "writer_kind": context.writer_kind if context else "UNREGISTERED",
                "access_mode": context.access_mode if context else "write",
                "operation": context.operation if context else "unregistered",
                "api_id": context.api_id if context else "",
                "source": context.source if context else "",
                "parent_call_id": context.parent_call_id if context else "",
                "request_id": context.request_id if context else "",
                "backend_pid": None,
                "rows_attempted": context.rows_attempted if context else None,
                "calls": 1, "transactions": 0, "commits": 0, "rollbacks": 0,
                "sql_calls": 0, "connection_acquire_ms": _milliseconds(started_mono),
                "execute_windows": [], "execute_windows_truncated": False,
                "execute_ms": 0.0, "commit_ms": None, "rollback_ms": None,
                "commit_started_at": None, "commit_finished_at": None,
                "rollback_started_at": None, "rollback_finished_at": None,
                "close_ms": None, "total_ms": _milliseconds(started_mono),
                "outcome": "connect_error",
                "errors": [{"stage": "connect", "exception_type": type(error).__name__}],
                "retries": None,
            }
            try:
                record_db_call(record, capture_token=capture_token)
            except Exception:
                pass
        raise
    return ObservedDBConnection(
        raw, context, started_at=started_at, started_mono=started_mono,
        connect_ms=_milliseconds(started_mono), capture_token=capture_token,
        hook=hook,
    )
