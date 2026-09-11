from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from itertools import count
from typing import Any, Callable, Protocol

from .database import QueryStore, StoredQuery


logger = logging.getLogger(__name__)
MAX_MEMORY_CACHE_ENTRIES = 2_048


class RestClient(Protocol):
    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[dict[str, Any], bool, str]: ...


READ_ONLY_ENDPOINTS = {
    "ka00198": "/api/dostk/stkinfo",
    "ka10016": "/api/dostk/stkinfo",
    "ka10001": "/api/dostk/stkinfo",
    "ka10100": "/api/dostk/stkinfo",
    "ka10080": "/api/dostk/chart",
    "ka10081": "/api/dostk/chart",
    "ka10083": "/api/dostk/chart",
    "ka10094": "/api/dostk/chart",
    "ka10045": "/api/dostk/mrkcond",
    "ka90008": "/api/dostk/mrkcond",
    "ka20005": "/api/dostk/chart",
    "ka20006": "/api/dostk/chart",
    "kt00007": "/api/dostk/acnt",
    "kt00015": "/api/dostk/acnt",
}

# 숫자가 작을수록 먼저 처리한다. 화면과 체결 스냅샷을 과거 백필보다 앞세운다.
REQUEST_PRIORITIES = {
    "ka00198": 10,
    "ka10016": 15,
    "ka10045": 20,
    "ka90008": 25,
    "ka10001": 35,
    "ka10100": 35,
    "kt00007": 45,
    "kt00015": 50,
    "ka10080": 70,
    "ka10081": 75,
    "ka20005": 75,
    "ka20006": 75,
    "ka10083": 90,
    "ka10094": 95,
}

CACHE_SECONDS = {
    "ka00198": 0.5,
    "ka10016": 10.0,
    "ka10001": 300.0,
    "ka10100": 86_400.0,
    "ka10080": 2.0,
    "ka10081": 30.0,
    "ka10083": 300.0,
    "ka10094": 300.0,
    "ka10045": 2.0,
    "ka90008": 2.0,
    "ka20005": 2.0,
    "ka20006": 30.0,
}


@dataclass(frozen=True)
class BrokerResult:
    payload: dict[str, Any]
    has_next: bool
    next_key: str
    cache_hit: bool = False


@dataclass
class _Job:
    key: str
    api_id: str
    path: str
    body: dict[str, Any]
    cont_yn: str
    next_key: str
    future: asyncio.Future[BrokerResult]


class CentralRestBroker:
    """한 KiwoomRestClient를 통해 모든 REST 요청을 직렬 처리한다."""

    def __init__(
        self, client: RestClient, store: QueryStore | None = None,
        response_handler: Callable[[str, dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._response_handler = response_handler
        self._queue: asyncio.PriorityQueue[tuple[int, int, _Job | None]] = asyncio.PriorityQueue()
        self._sequence = count()
        self._inflight: dict[str, asyncio.Future[BrokerResult]] = {}
        self._cache: dict[str, tuple[float, BrokerResult]] = {}
        self._guard = asyncio.Lock()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="kiwoom-central-rest-broker")

    async def close(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        await self._queue.put((10_000, next(self._sequence), None))
        await worker

    async def request(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> BrokerResult:
        self._validate(api_id, path, cont_yn, next_key)
        await self.start()
        key = self._fingerprint(api_id, path, body, cont_yn, next_key)
        async with self._guard:
            self._prune_memory_cache()
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                value = cached[1]
                return BrokerResult(copy.deepcopy(value.payload), value.has_next, value.next_key, True)
            ttl = CACHE_SECONDS.get(api_id, 0.0) if cont_yn == "N" else 0.0
            if ttl > 0 and self._store is not None:
                stored = await asyncio.to_thread(self._store.load_query, key)
                if stored is not None:
                    if self._response_handler is not None:
                        try:
                            await asyncio.to_thread(self._response_handler, api_id, body, stored.payload)
                        except Exception:
                            logger.exception("중앙 캐시 응답 저장에 실패했습니다: %s", api_id)
                    result = BrokerResult(stored.payload, stored.has_next, stored.next_key, True)
                    self._cache[key] = (time.monotonic() + ttl, result)
                    return copy.deepcopy(result)
            existing = self._inflight.get(key)
            if existing is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                job = _Job(key, api_id, path, copy.deepcopy(body), cont_yn, next_key, future)
                await self._queue.put((REQUEST_PRIORITIES[api_id], next(self._sequence), job))
                existing = future
        return await asyncio.shield(existing)

    async def _run(self) -> None:
        while True:
            _, _, job = await self._queue.get()
            try:
                if job is None:
                    return
                try:
                    payload, has_next, next_key = await asyncio.to_thread(
                        self._client.request_with_continuation,
                        job.api_id, job.path, job.body, cont_yn=job.cont_yn, next_key=job.next_key,
                    )
                    result = BrokerResult(payload, has_next, next_key)
                    if self._response_handler is not None:
                        try:
                            await asyncio.to_thread(self._response_handler, job.api_id, job.body, payload)
                        except Exception:
                            # 저장 보완 실패가 화면 조회 성공까지 실패로 바꾸면 안 된다.
                            logger.exception("중앙 조회 응답 저장에 실패했습니다: %s", job.api_id)
                    ttl = CACHE_SECONDS.get(job.api_id, 0.0) if job.cont_yn == "N" else 0.0
                    if ttl > 0:
                        async with self._guard:
                            self._cache[job.key] = (time.monotonic() + ttl, result)
                            self._prune_memory_cache()
                        if self._store is not None:
                            await asyncio.to_thread(
                                self._store.save_query, job.key, job.api_id, time.time() + ttl,
                                StoredQuery(result.payload, result.has_next, result.next_key),
                            )
                    if not job.future.done():
                        job.future.set_result(result)
                except Exception as error:
                    if not job.future.done():
                        job.future.set_exception(error)
                finally:
                    async with self._guard:
                        self._inflight.pop(job.key, None)
            finally:
                self._queue.task_done()

    def _prune_memory_cache(self) -> None:
        """만료 응답과 오래된 응답을 제거해 24시간 실행 시 메모리를 제한한다."""
        now = time.monotonic()
        expired = tuple(key for key, (expires_at, _) in self._cache.items() if expires_at <= now)
        for key in expired:
            self._cache.pop(key, None)
        overflow = len(self._cache) - MAX_MEMORY_CACHE_ENTRIES
        if overflow > 0:
            for key, _ in sorted(self._cache.items(), key=lambda item: item[1][0])[:overflow]:
                self._cache.pop(key, None)

    @staticmethod
    def _validate(api_id: str, path: str, cont_yn: str, next_key: str) -> None:
        expected = READ_ONLY_ENDPOINTS.get(api_id)
        if expected is None:
            raise ValueError(f"중앙 서버에서 허용하지 않는 조회 API입니다: {api_id}")
        if path != expected:
            raise ValueError(f"{api_id}의 요청 경로가 올바르지 않습니다.")
        if cont_yn not in {"N", "Y"}:
            raise ValueError("cont_yn은 N 또는 Y여야 합니다.")
        if cont_yn == "Y" and not next_key:
            raise ValueError("연속조회에는 next_key가 필요합니다.")

    @staticmethod
    def _fingerprint(api_id: str, path: str, body: dict[str, Any], cont_yn: str, next_key: str) -> str:
        raw = json.dumps([api_id, path, body, cont_yn, next_key], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
