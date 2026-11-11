"""Explicit append/read routes for a configured observation archive receiver."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.requests import ClientDisconnect

from kiwoom_monitor.domain.observation_archive import (
    MAX_OBSERVATION_ARCHIVE_BYTES, NEWS_BODY_ARCHIVE_KIND, OBSERVATION_ARCHIVE_PATH, ObservationArchiveConflict,
    archive_batch_id,
)

from .async_lifetime import await_native_call
from kiwoom_monitor.domain.pc_history_archive import RESEARCH_FILE_SOURCE, valid_history_source


def _find_news_body(value: dict[str, Any], body_id: str) -> dict[str, Any] | None:
    if value["receipt"]["kind"] == NEWS_BODY_ARCHIVE_KIND:
        for line in value["batch"]["observations"].splitlines():
            record = json.loads(line)
            if record["body"]["body_revision_id"] == body_id:
                return record
    return None


class ObservationArchiveStore(Protocol):
    def accept_observation_archive(self, source_stream: str, raw: bytes, expected_sha256: str) -> dict[str, Any]: ...

    def load_observation_archive(
        self, source_stream: str, batch_id: str, *, include_body: bool = False,
    ) -> dict[str, Any] | None: ...

    def load_pc_archive_history(self, source_stream: str, source: str, subject: str,
                                first: str, last: str, limit: int, batch_id: str = "") -> list[dict]: ...


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

    @router.get("/pc-history")
    async def pc_history(
        source: str = Query(max_length=120), subject: str = Query(default="", max_length=120),
        first: str = Query(default="", max_length=100), last: str = Query(default="", max_length=100),
        limit: int = Query(default=1000, ge=1, le=10000),
        batch_id: str = Query(default="", max_length=36),
        source_stream: str = Depends(authorize_archive_stream),
    ) -> dict[str, Any]:
        if not valid_history_source(source):
            raise HTTPException(status_code=404, detail="unknown PC history source")
        if source == RESEARCH_FILE_SOURCE and (limit > 2 or not (subject or batch_id)):
            raise HTTPException(status_code=400, detail="request at most two chunks of one research file")
        if batch_id:
            try:
                archive_batch_id(batch_id)
            except ValueError as error:
                raise HTTPException(status_code=400, detail="invalid PC history batch ID") from error
        records = await await_native_call(
            store.load_pc_archive_history, source_stream, source, subject, first, last, limit, batch_id)
        return {"records": records}

    @router.get("/{batch_id}")
    async def load_observation_archive_batch(
        batch_id: str, source_stream: str = Depends(authorize_archive_stream),
        news_body_id: str = Query(default="", max_length=200),
    ) -> dict[str, Any]:
        value = await load(source_stream, batch_id, include_body=True)
        if not news_body_id:
            return value
        record = await await_native_call(_find_news_body, value, news_body_id)
        if record is None:
            raise HTTPException(status_code=404, detail="archived news body not found")
        return {"receipt": value["receipt"], "record": record}

    return router
