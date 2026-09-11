from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(eq=False)
class RealtimeSubscriber:
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    codes: set[str] = field(default_factory=set)
    nxt_codes: set[str] = field(default_factory=set)


class RealtimeHub:
    """키움 실시간 이벤트 한 벌을 여러 데스크톱 앱에 복제한다."""

    def __init__(self) -> None:
        self._subscribers: set[RealtimeSubscriber] = set()
        self._upstream_codes: set[str] = set()
        self._upstream_ready = False

    def connect(self) -> RealtimeSubscriber:
        subscriber = RealtimeSubscriber()
        self._subscribers.add(subscriber)
        return subscriber

    def disconnect(self, subscriber: RealtimeSubscriber) -> None:
        self._subscribers.discard(subscriber)

    def update_subscription(
        self, subscriber: RealtimeSubscriber, codes: list[str], nxt_codes: list[str],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        subscriber.codes = {str(code).strip() for code in codes if str(code).strip()}
        subscriber.nxt_codes = {str(code).strip() for code in nxt_codes if str(code).strip()}
        return self.requested_codes()

    def requested_codes(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        codes = set().union(*(client.codes for client in self._subscribers)) if self._subscribers else set()
        nxt_codes = set().union(*(client.nxt_codes for client in self._subscribers)) if self._subscribers else set()
        return tuple(sorted(codes)), tuple(sorted(nxt_codes))

    def publish(self, event: dict[str, Any], code: str = "") -> None:
        for subscriber in tuple(self._subscribers):
            if code and subscriber.codes and code not in subscriber.codes:
                continue
            if subscriber.queue.full():
                try:
                    subscriber.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            subscriber.queue.put_nowait(event)

    def set_upstream_ready(self, codes: tuple[str, ...], ready: bool) -> None:
        self._upstream_codes = set(codes) if ready else set()
        self._upstream_ready = ready

    def upstream_ready_for(self, codes: set[str]) -> bool:
        return self._upstream_ready and codes.issubset(self._upstream_codes)

    @property
    def upstream_ready(self) -> bool:
        """NAS와 키움 사이의 현재 실시간 원본 연결 여부."""
        return self._upstream_ready

    @property
    def client_count(self) -> int:
        return len(self._subscribers)
