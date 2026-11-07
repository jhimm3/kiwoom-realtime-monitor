"""Explicit append/read routes for a configured observation archive receiver."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.requests import ClientDisconnect

from kiwoom_monitor.domain.observation_archive import (
    MAX_OBSERVATION_ARCHIVE_BYTES, OBSERVATION_ARCHIVE_PATH, ObservationArchiveConflict, archive_batch_id,
)

from .async_lifetime import await_native_call


class ObservationArchiveStore(Protocol):
    def accept_observation_archive(self, source_stream: str, raw: bytes, expected_sha256: str) -> dict[str, Any]: ...

    def load_observation_archive(
        self, source_stream: str, batch_id: str, *, include_body: bool = False,
    ) -> dict[str, Any] | None: ...


def create_observation_archive_router(
    store: ObservationArchiveStore, authorize_archive_stream: Callable[..., str],
) -> APIRouter:
    router = APIRouter(prefix=OBSERVATION_ARCHIVE_PATH)

    @router.post("")
    async def accept_observation_archive(
        request: Request, sha256: str = Query(pattern=r"^[0-9a-f]{64}$"),
        source_stream: str = Depends(authorize_archive_stream),
    ) -> dict[str, Any]:
        body = bytearray()
        try:
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_OBSERVATION_ARCHIVE_BYTES:
                    raise HTTPException(status_code=413, detail="observation archive batch exceeds the input budget")
                body.extend(chunk)
        except ClientDisconnect as error:
            raise HTTPException(status_code=400, detail="incomplete observation archive upload") from error
        try:
            # Cancellation joins the native commit/close; a later retry uses the first receipt.
            return await await_native_call(store.accept_observation_archive, source_stream, bytes(body), sha256)
        except ObservationArchiveConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (UnicodeError, ValueError, RecursionError) as error:
            raise HTTPException(status_code=400, detail="invalid observation archive upload") from error

    async def load(source_stream: str, batch_id: str, *, include_body: bool) -> dict[str, Any]:
        try:
            archive_batch_id(batch_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid observation archive batch ID") from error
        value = await await_native_call(store.load_observation_archive, source_stream, batch_id, include_body=include_body)
        if value is None:
            raise HTTPException(status_code=404, detail="observation archive batch not found")
        return value

    @router.get("/{batch_id}/receipt")
    async def load_observation_archive_receipt(
        batch_id: str, source_stream: str = Depends(authorize_archive_stream),
    ) -> dict[str, Any]:
        return await load(source_stream, batch_id, include_body=False)

    @router.get("/{batch_id}")
    async def load_observation_archive_batch(
        batch_id: str, source_stream: str = Depends(authorize_archive_stream),
    ) -> dict[str, Any]:
        return await load(source_stream, batch_id, include_body=True)

    return router
