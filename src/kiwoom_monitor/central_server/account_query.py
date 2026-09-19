"""Verified account-query sessions for cursor-safe NAS pagination."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from kiwoom_monitor.domain.order_contract import AccountBinding

from .rest_broker import CentralRestBroker


ACCOUNT_QUERY_ENDPOINTS = {
    "kt00007": "/api/dostk/acnt",
    "kt00015": "/api/dostk/acnt",
}


@dataclass
class _Session:
    api_id: str
    path: str
    body_hash: str
    binding: AccountBinding
    next_page_index: int
    next_key: str
    expires_at: float


class AccountQuerySessionManager:
    """Owns account cursor state; raw account numbers never cross this boundary."""

    def __init__(
        self, broker: CentralRestBroker,
        binding_provider: Callable[[], AccountBinding | None], *,
        ttl_seconds: float = 120.0,
        monotonic_provider: Callable[[], float] = time.monotonic,
        read_limiter: asyncio.Semaphore | None = None,
    ) -> None:
        self._broker = broker
        self._binding_provider = binding_provider
        self._ttl = max(10.0, float(ttl_seconds))
        self._monotonic = monotonic_provider
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()
        self._read_limiter = read_limiter
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self._credential_paused = False
        self._credential_drain_task: asyncio.Task | None = None

    async def begin_credential_change(self) -> None:
        if self._closed:
            raise RuntimeError("ACCOUNT_QUERY_CLOSED")
        self._credential_paused = True
        if self._credential_drain_task is None:
            self._credential_drain_task = asyncio.create_task(
                self._drain_credential_queries(), name="account-query-credential-drain")
        await asyncio.shield(self._credential_drain_task)

    async def _drain_credential_queries(self) -> None:
        await asyncio.gather(*(asyncio.shield(task) for task in tuple(self._tasks)), return_exceptions=True)

    def end_credential_change(self, *, invalidate_cursors: bool) -> None:
        if self._closed:
            raise RuntimeError("ACCOUNT_QUERY_CLOSED")
        if self._credential_drain_task is None or not self._credential_drain_task.done():
            raise RuntimeError("ACCOUNT_QUERY_DRAIN_NOT_COMPLETE")
        self._credential_drain_task.result()
        if invalidate_cursors:
            self._sessions.clear()
        self._credential_drain_task = None
        self._credential_paused = False

    async def close(self) -> None:
        self._closed = True
        await asyncio.gather(*(asyncio.shield(t) for t in tuple(self._tasks)), return_exceptions=True)
        if self._credential_drain_task is not None:
            await asyncio.shield(self._credential_drain_task)
        self._sessions.clear()

    def rebind_drained_broker(self, broker, *, read_limiter=None):
        """Move routing without changing this account's binding or cursor sessions."""
        if (self._closed or not self._credential_paused or self._credential_drain_task is None
                or not self._credential_drain_task.done()):
            raise RuntimeError("ACCOUNT_QUERY_DRAIN_NOT_COMPLETE")
        self._credential_drain_task.result()
        self._broker, self._read_limiter = broker, read_limiter

    async def query(
        self, *, api_id: str, path: str, body: dict[str, Any],
        batch_id: str = "", page_index: int = 0, next_key: str = "",
    ) -> dict[str, object]:
        if self._closed:
            raise RuntimeError("ACCOUNT_QUERY_CLOSED")
        if self._credential_paused:
            raise RuntimeError("ACCOUNT_QUERY_BUSY")
        if len(self._tasks) >= 32:
            raise RuntimeError("ACCOUNT_QUERY_BUSY")
        task = asyncio.create_task(self._query(api_id=api_id, path=path, body=body,
            batch_id=batch_id, page_index=page_index, next_key=next_key), name="owned-account-query")
        self._tasks.add(task)
        def completed(done):
            self._tasks.discard(done)
            if not done.cancelled(): done.exception()
        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def _query(self, *, api_id, path, body, batch_id, page_index, next_key):
        if ACCOUNT_QUERY_ENDPOINTS.get(api_id) != path:
            raise ValueError("account-query does not allow this API endpoint")
        body_hash = hashlib.sha256(json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        async with self._lock:
            if self._closed: raise RuntimeError("ACCOUNT_QUERY_CLOSED")
            now = self._monotonic()
            self._sessions = {
                key: value for key, value in self._sessions.items()
                if value.expires_at > now
            }
            current_binding = self._binding_provider()
            if current_binding is None:
                raise RuntimeError("ACCOUNT_IDENTITY_UNVERIFIED")
            if not batch_id:
                if len(self._sessions) >= 256:
                    raise RuntimeError("ACCOUNT_QUERY_BUSY")
                if page_index != 0 or next_key:
                    raise ValueError("first account-query page must start without a cursor")
                batch_id = uuid.uuid4().hex
                session = _Session(
                    api_id, path, body_hash, current_binding, 0, "", now + self._ttl,
                )
                self._sessions[batch_id] = session
            else:
                session = self._sessions.get(batch_id)
                if session is None:
                    raise ValueError("ACCOUNT_QUERY_CURSOR_EXPIRED")
                if (
                    session.api_id != api_id or session.path != path
                    or session.body_hash != body_hash
                ):
                    raise ValueError("ACCOUNT_QUERY_BODY_MISMATCH")
                if session.next_page_index != page_index or session.next_key != next_key:
                    raise ValueError("ACCOUNT_QUERY_CURSOR_MISMATCH")
                if session.binding != current_binding:
                    self._sessions.pop(batch_id, None)
                    raise RuntimeError("ACCOUNT_CONTEXT_MISMATCH")
            try:
                if self._read_limiter is None:
                    result = await self._broker.request(api_id, path, body,
                        cont_yn="Y" if page_index else "N", next_key=next_key)
                else:
                    async with self._read_limiter:
                        if self._closed: raise RuntimeError("ACCOUNT_QUERY_CLOSED")
                        result = await self._broker.request(api_id, path, body,
                            cont_yn="Y" if page_index else "N", next_key=next_key)
            except Exception:
                self._sessions.pop(batch_id, None)
                raise
            if self._closed or self._binding_provider() != session.binding:
                self._sessions.pop(batch_id, None)
                raise RuntimeError("ACCOUNT_CONTEXT_MISMATCH")
            complete = not result.has_next
            if result.has_next and not result.next_key:
                self._sessions.pop(batch_id, None)
                raise RuntimeError("ACCOUNT_QUERY_CURSOR_MISSING")
            if complete:
                self._sessions.pop(batch_id, None)
            else:
                session.next_page_index = page_index + 1
                session.next_key = result.next_key
                session.expires_at = self._monotonic() + self._ttl
            binding = session.binding
            return {
                "payload": result.payload,
                "batch_id": batch_id,
                "page_index": page_index,
                "complete": complete,
                "next_key": result.next_key,
                "context": {
                    **binding.scope.to_dict(),
                    "credential_profile_id": binding.credential_profile_id,
                    "binding_revision": binding.binding_revision,
                    "verified_at": binding.verified_at.isoformat(),
                    "verification_method": binding.verification_method,
                },
                "provenance": {"transport": "nas", "cache_hit": result.cache_hit},
            }
