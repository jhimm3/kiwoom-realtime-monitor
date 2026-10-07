from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CapturedMessageReceipt:
    trace_id: str
    producer_component: str
    message_id: str
    input_ids: tuple[str, ...]
    complete: bool
    compact_context: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class CapturedParserReceipt:
    message: CapturedMessageReceipt
    kind: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class CapturedControlReceipt:
    trace_id: str
    producer_component: str
    input_id: str
    kind: str
    complete: bool
    compact_context: Any = field(default=None, compare=False, repr=False)


class CapturedHubEvent(dict):
    """Private queue metadata; JSON and public dictionary keys stay unchanged."""
    def __init__(self, event, *, source=None, delivery=None):
        super().__init__(event)
        self.source_receipt = source
        self.delivery_receipt = delivery


@dataclass(eq=False)
class RealtimeSubscriber:
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    codes: set[str] = field(default_factory=set)
    nxt_codes: set[str] = field(default_factory=set)
    program_codes: set[str] = field(default_factory=set)
    priority_codes: tuple[str, ...] = ()
    dropped_events: int = 0
    capture_component: str = ""
    capture_epoch: str = ""
    capture_sequence: int = 0
    capture_context: Any = field(default=None, repr=False)


class RealtimeHub:
    """키움 실시간 이벤트 한 벌을 여러 데스크톱 앱에 복제한다."""

    def __init__(self) -> None:
        self._subscribers: set[RealtimeSubscriber] = set()
        self._upstream_codes: set[str] = set()
        self._upstream_ready = False

    def connect(self, *, capture_component: str = "") -> RealtimeSubscriber:
        from .diagnostic_top20_lifecycle_input import hub_initial, record_intent, reject
        try:
            hub_initial(self)
        except Exception:
            reject(self, "top20_realtime_initial_invalid")
        subscriber = RealtimeSubscriber(capture_component=capture_component)
        self._subscribers.add(subscriber)
        record_intent(self, subscriber, "connect")
        return subscriber

    def disconnect(self, subscriber: RealtimeSubscriber) -> None:
        from .diagnostic_top20_lifecycle_input import hub_initial, record_intent, reject
        try:
            hub_initial(self)
        except Exception:
            reject(self, "top20_realtime_initial_invalid")
        self._subscribers.discard(subscriber)
        record_intent(self, subscriber, "disconnect")

    def update_subscription(
        self, subscriber: RealtimeSubscriber, codes: list[str], nxt_codes: list[str],
        program_codes: list[str] | None = None,
        priority_codes: list[str] | None = None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        from .diagnostic_top20_lifecycle_input import hub_initial, record_intent, reject
        try:
            hub_initial(self)
        except Exception:
            reject(self, "top20_realtime_initial_invalid")
        subscriber.codes = {str(code).strip() for code in codes if str(code).strip()}
        subscriber.nxt_codes = {str(code).strip() for code in nxt_codes if str(code).strip()}
        requested_program_codes = codes if program_codes is None else program_codes
        subscriber.program_codes = {
            str(code).strip() for code in requested_program_codes if str(code).strip()
        }
        subscriber.priority_codes = tuple(dict.fromkeys(
            str(code).strip() for code in (priority_codes or ())
            if str(code).strip() in subscriber.codes
        ))
        record_intent(self, subscriber, "update")
        return self.requested_codes()

    def requested_codes(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        codes = set().union(*(client.codes for client in self._subscribers)) if self._subscribers else set()
        nxt_codes = set().union(*(client.nxt_codes for client in self._subscribers)) if self._subscribers else set()
        return tuple(sorted(codes)), tuple(sorted(nxt_codes))

    def requested_program_codes(self) -> tuple[str, ...]:
        codes = (
            set().union(*(client.program_codes for client in self._subscribers))
            if self._subscribers else set()
        )
        priority = self.requested_priority_codes()
        return tuple((*priority, *(code for code in sorted(codes) if code not in priority)))

    def requested_priority_codes(self) -> tuple[str, ...]:
        """TOP20·실매수처럼 제한 시 먼저 보존할 종목을 순서대로 반환한다."""
        positions: dict[str, int] = {}
        for subscriber in self._subscribers:
            for index, code in enumerate(subscriber.priority_codes):
                positions[code] = min(index, positions.get(code, index))
        return tuple(sorted(positions, key=lambda code: (positions[code], code)))

    def publish(self, event: dict[str, Any], code: str = "") -> None:
        from .diagnostic_top20_lifecycle_input import record_hub_event
        event = record_hub_event(self, event)
        capture_active = False
        if any(subscriber.capture_component for subscriber in self._subscribers):
            from .diagnostic_trace import input_token
            capture_active = input_token("top20_inputs") is not None
        if capture_active:
            from .diagnostic_top20_input import queue_delivery, dropped_delivery
        for subscriber in tuple(self._subscribers):
            if code and subscriber.codes and code not in subscriber.codes:
                continue
            if subscriber.queue.full():
                try:
                    discarded = subscriber.queue.get_nowait()
                    subscriber.dropped_events += 1
                    if capture_active:
                        dropped_delivery(subscriber, discarded)
                except asyncio.QueueEmpty:
                    pass
            subscriber.queue.put_nowait(queue_delivery(subscriber, event) if capture_active else event)

    def set_upstream_ready(self, codes: tuple[str, ...], ready: bool, *, approval=None) -> None:
        from .diagnostic_top20_lifecycle_input import record_ready
        record_ready(self, codes, ready, approval)
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
