from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Callable, Protocol

from .database import QueryStore, StoredQuery


logger = logging.getLogger(__name__)
MAX_MEMORY_CACHE_ENTRIES = 2_048
RANKING_RESERVATION_LEAD_SECONDS = 5.0
RANKING_RESERVATION_RELEASE_SECONDS = 0.35


class RestClient(Protocol):
    def prepare_credential_token(self, settings: Any) -> Any: ...
    def verify_prepared_credentials(self, prepared: Any) -> Any: ...
    def prepare_drained_credentials(self, settings: Any) -> Any: ...
    def begin_credential_change(self) -> None: ...
    def activate_prepared_credentials(self, prepared: Any) -> int: ...
    def end_credential_change(self) -> None: ...
    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[dict[str, Any], bool, str]: ...


ACCOUNT_RECOVERY_ENDPOINTS = {key: "/api/dostk/acnt" for key in ("ka10075", "ka10076", "kt00018", "kt00001")}

READ_ONLY_ENDPOINTS = {
    **ACCOUNT_RECOVERY_ENDPOINTS,
    "ka00001": "/api/dostk/acnt",
    "ka00198": "/api/dostk/stkinfo",
    "ka10016": "/api/dostk/stkinfo",
    "ka10001": "/api/dostk/stkinfo",
    "ka10100": "/api/dostk/stkinfo",
    "ka10080": "/api/dostk/chart",
    "ka10081": "/api/dostk/chart",
    "ka10083": "/api/dostk/chart",
    "ka10094": "/api/dostk/chart",
    "ka10045": "/api/dostk/mrkcond",
    "ka10054": "/api/dostk/stkinfo",
    "ka90008": "/api/dostk/mrkcond",
    "ka20005": "/api/dostk/chart",
    "ka20006": "/api/dostk/chart",
    "kt00007": "/api/dostk/acnt",
    "kt00015": "/api/dostk/acnt",
}
MOCK_ACCOUNT_ENDPOINTS = {
    "kt00007": "/api/dostk/acnt",  # scoped historical fills
    "kt00015": "/api/dostk/acnt",  # scoped historical costs
    "ka00001": "/api/dostk/acnt",  # verified account identity
    "kt00001": "/api/dostk/acnt",  # orderable cash
    "ka10075": "/api/dostk/acnt",  # unfilled orders
    "ka10076": "/api/dostk/acnt",  # fills
    "kt00018": "/api/dostk/acnt",  # evaluated balance and positions
}

# 숫자가 작을수록 먼저 처리한다. 화면과 체결 스냅샷을 과거 백필보다 앞세운다.
REQUEST_PRIORITIES = {
    "ka00001": 20,
    "ka00198": 10,
    "ka10075": 20,
    "ka10076": 20,
    "kt00001": 25,
    "kt00018": 25,
    "ka10016": 15,
    "ka10045": 20,
    "ka90008": 25,
    "ka10001": 35,
    "ka10100": 35,
    "kt00007": 45,
    "kt00015": 50,
    "ka10054": 65,
    "ka10080": 70,
    "ka10081": 75,
    "ka20005": 75,
    "ka20006": 75,
    "ka10083": 90,
    "ka10094": 95,
}


def ranking_reservation_delay(epoch_seconds: float) -> float:
    """Keep the market broker free just before each 30-second ranking boundary."""
    phase = float(epoch_seconds) % 30.0
    remaining = 30.0 - phase
    if 0.0 < remaining <= RANKING_RESERVATION_LEAD_SECONDS:
        return remaining + RANKING_RESERVATION_RELEASE_SECONDS
    return 0.0

CACHE_SECONDS = {
    # 순위 경계에서 직전 dt/tm이 오면 0.25초 뒤 실제 재조회할 수 있어야 한다.
    "ka00198": 0.2,
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


@dataclass
class _CredentialJob:
    settings: Any = field(repr=False)
    future: asyncio.Future[Any] = field(repr=False)
    phase: str = "token"


class BrokerCredentialBusyError(RuntimeError):
    pass


class CentralRestBroker:
    """한 KiwoomRestClient를 통해 모든 REST 요청을 직렬 처리한다."""

    def __init__(
        self, client: RestClient, store: QueryStore | None = None,
        response_handler: Callable[[str, dict[str, Any], dict[str, Any]], None] | None = None,
        *, allowed_endpoints: dict[str, str] | None = None, namespace: str = "market",
        ranking_reservation: bool = False,
    ) -> None:
        self._client = client
        self._store = store
        self._response_handler = response_handler
        self._allowed_endpoints = dict(allowed_endpoints or READ_ONLY_ENDPOINTS)
        self._namespace = namespace.strip()
        self._ranking_reservation = bool(ranking_reservation)
        if not self._namespace:
            raise ValueError("broker namespace is required")
        self._queue: asyncio.PriorityQueue[tuple[int, int, _Job | _CredentialJob | None]] = asyncio.PriorityQueue()
        self._sequence = count()
        self._inflight: dict[str, asyncio.Future[BrokerResult]] = {}
        self._cache: dict[str, tuple[float, BrokerResult]] = {}
        self._guard = asyncio.Lock()
        self._worker: asyncio.Task[None] | None = None
        self._credential_paused = False
        self._credential_generation = 0
        self._drain_task: asyncio.Task[None] | None = None
        self._activation_task: asyncio.Task[int] | None = None
        self._activating_credentials: Any = None
        self._resume_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None

    async def prepare_credentials(self, settings: Any) -> Any:
        """Explicit management job, lower priority than live ranking."""
        candidate = await self._prepare_credential_phase(settings, "token")
        return await self._prepare_credential_phase(candidate, "identity")

    async def prepare_drained_credentials(self, settings: Any) -> Any:
        """Explicit recovery remains queued; ordinary old-generation reads stay closed."""
        await self.start()
        async with self._guard:
            if (not self._credential_paused or self._drain_task is None or not self._drain_task.done()
                    or (self._activation_task is not None and not self._activation_task.done())):
                raise BrokerCredentialBusyError("CREDENTIAL_DRAIN_REQUIRED")
            self._drain_task.result()
            if self._activation_task is not None:
                if not self._activation_task.cancelled():
                    self._activation_task.exception()
                self._activation_task = None
                self._activating_credentials = None
            future = asyncio.get_running_loop().create_future()
            future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            await self._queue.put((20, next(self._sequence), _CredentialJob(settings, future, "drained")))
        return await asyncio.shield(future)

    async def _prepare_credential_phase(self, settings: Any, phase: str) -> Any:
        await self.start()
        async with self._guard:
            if self._credential_paused:
                raise BrokerCredentialBusyError("CREDENTIAL_CHANGE_IN_PROGRESS")
            future = asyncio.get_running_loop().create_future()
            future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            await self._queue.put((20, next(self._sequence), _CredentialJob(settings, future, phase)))
        return await asyncio.shield(future)

    async def begin_credential_change(self) -> None:
        async with self._guard:
            self._credential_paused = True
            if self._drain_task is None:
                self._drain_task = asyncio.create_task(self._drain_credentials(), name="credential-broker-drain")
        # Cancelling the waiter never cancels actual HTTP/DB work or token-refresh drain.
        await asyncio.shield(self._drain_task)

    async def _drain_credentials(self) -> None:
        await self._queue.join()
        await asyncio.to_thread(self._client.begin_credential_change)

    async def activate_prepared_credentials(self, prepared: Any) -> int:
        if not self._credential_paused or self._drain_task is None or not self._drain_task.done():
            raise BrokerCredentialBusyError("CREDENTIAL_DRAIN_NOT_COMPLETE")
        self._drain_task.result()
        if self._activation_task is None:
            self._activating_credentials = prepared
            self._activation_task = asyncio.create_task(asyncio.to_thread(
                self._client.activate_prepared_credentials, prepared,
            ), name="credential-client-activate")
        elif self._activating_credentials is not prepared:
            raise BrokerCredentialBusyError("CREDENTIAL_CANDIDATE_CONFLICT")
        return await asyncio.shield(self._activation_task)

    async def end_credential_change(self) -> None:
        if self._resume_task is None:
            self._resume_task = asyncio.create_task(self._end_credential_change(), name="credential-broker-resume")
        try:
            await asyncio.shield(self._resume_task)
        finally:
            if self._resume_task is not None and self._resume_task.done():
                self._resume_task = None

    def swap_drained_clients(self, other: CentralRestBroker) -> None:
        """Keep queue ownership while moving two already drained account transports."""
        if other is self or self._client.environment != other._client.environment:
            raise BrokerCredentialBusyError("CREDENTIAL_ENVIRONMENT_CONFLICT")
        for broker in (self, other):
            if (not broker._credential_paused or broker._drain_task is None or not broker._drain_task.done()
                    or broker._activation_task is not None or broker._resume_task is not None):
                raise BrokerCredentialBusyError("CREDENTIAL_DRAIN_NOT_COMPLETE")
            broker._drain_task.result()
        # No await between validation and exchange; physical locks/rate history
        # travel with the original client, never cloned into a second requester.
        self._client, other._client = other._client, self._client
        for broker in (self, other):
            broker._credential_generation += 1
            broker._cache.clear()

    async def _end_credential_change(self) -> None:
        try:
            await self._resume_credentials()
        finally:
            if self._resume_task is asyncio.current_task():
                self._resume_task = None

    async def _resume_credentials(self) -> None:
        async with self._guard:
            if self._drain_task is None or not self._drain_task.done():
                raise BrokerCredentialBusyError("CREDENTIAL_DRAIN_NOT_COMPLETE")
            self._drain_task.result()
            if self._activation_task is not None and not self._activation_task.done():
                raise BrokerCredentialBusyError("CREDENTIAL_ACTIVATION_NOT_COMPLETE")
            generation = self._activation_task.result() if self._activation_task is not None else None
            await asyncio.to_thread(self._client.end_credential_change)
            if generation is not None:
                self._credential_generation = max(self._credential_generation + 1, generation)
                self._cache.clear()
            self._activation_task = None
            self._activating_credentials = None
            self._drain_task = None
            self._credential_paused = False

    async def start(self) -> None:
        if self._close_task is not None:
            if not self._close_task.done():
                raise BrokerCredentialBusyError("BROKER_CLOSE_IN_PROGRESS")
            self._close_task.result()
            self._close_task = None
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="kiwoom-central-rest-broker")

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="kiwoom-broker-close")
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            await self._queue.put((10_000, next(self._sequence), None))
            await worker
        for task in (self._drain_task, self._activation_task, self._resume_task):
            if task is not None:
                await asyncio.shield(task)

    async def request(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> BrokerResult:
        self._validate(api_id, path, cont_yn, next_key)
        await self.start()
        key = self._fingerprint(api_id, path, body, cont_yn, next_key)
        async with self._guard:
            if self._credential_paused:
                raise BrokerCredentialBusyError("CREDENTIAL_CHANGE_IN_PROGRESS")
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
                            logger.exception(
                                "recording_gap: 중앙 캐시 응답 저장에 실패했습니다: %s", api_id,
                            )
                    result = BrokerResult(stored.payload, stored.has_next, stored.next_key, True)
                    self._cache[key] = (time.monotonic() + ttl, result)
                    return copy.deepcopy(result)
            existing = self._inflight.get(key)
            if existing is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                job = _Job(key, api_id, path, copy.deepcopy(body), cont_yn, next_key, future)
                await self._queue.put((REQUEST_PRIORITIES.get(api_id, 50), next(self._sequence), job))
                existing = future
        return await asyncio.shield(existing)

    async def _run(self) -> None:
        while True:
            priority, _, job = await self._queue.get()
            try:
                if job is None:
                    return
                if isinstance(job, _CredentialJob):
                    try:
                        function = (self._client.prepare_drained_credentials if job.phase == "drained" else
                                    self._client.prepare_credential_token if job.phase == "token"
                                    else self._client.verify_prepared_credentials)
                        result = await asyncio.to_thread(function, job.settings)
                        if not job.future.done():
                            job.future.set_result(result)
                    except Exception:
                        if not job.future.done():
                            job.future.set_exception(RuntimeError("CANDIDATE_VALIDATION_FAILED"))
                    continue
                if (
                    self._ranking_reservation
                    and self._namespace == "market"
                    and priority > REQUEST_PRIORITIES["ka00198"]
                    and (delay := ranking_reservation_delay(time.time())) > 0
                ):
                    # The running HTTP call cannot be preempted. Delay starting a new
                    # low-priority call near the boundary, then requeue it so ka00198
                    # can overtake it as soon as the scheduler publishes the slot.
                    await asyncio.sleep(delay)
                    await self._queue.put((priority, next(self._sequence), job))
                    continue
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
                            logger.exception(
                                "recording_gap: 중앙 조회 응답 저장에 실패했습니다: %s", job.api_id,
                            )
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

    def _validate(self, api_id: str, path: str, cont_yn: str, next_key: str) -> None:
        expected = self._allowed_endpoints.get(api_id)
        if expected is None:
            raise ValueError(f"중앙 서버에서 허용하지 않는 조회 API입니다: {api_id}")
        if path != expected:
            raise ValueError(f"{api_id}의 요청 경로가 올바르지 않습니다.")
        if cont_yn not in {"N", "Y"}:
            raise ValueError("cont_yn은 N 또는 Y여야 합니다.")
        if cont_yn == "Y" and not next_key:
            raise ValueError("연속조회에는 next_key가 필요합니다.")

    def _fingerprint(self, api_id: str, path: str, body: dict[str, Any], cont_yn: str, next_key: str) -> str:
        raw = json.dumps(
            [self._namespace if self._credential_generation == 0 else
             f"{self._namespace}:credential-generation:{self._credential_generation}",
             api_id, path, body, cont_yn, next_key],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
