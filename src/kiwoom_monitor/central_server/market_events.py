"""VI와 저장 조건식 편입 종목의 서버측 사실 수집."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    StockPriceReference, TradeTick, ViEvent, parse_stock_price_references,
    parse_vi_events,
)

from .diagnostic_replay_contract import capture_owner, operation_identity
from .diagnostic_replay_runtime import owned_create_task, owned_to_thread
from .diagnostic_trace import input_token
from .realtime_hub import RealtimeHub, RealtimeSubscriber
from .rest_broker import CentralRestBroker


logger = logging.getLogger(__name__)


class MarketEventStore(Protocol):
    """MarketEventService가 사용하는 시장 사실·cohort 저장 계약."""

    def load_hot_cohort(self, *, active_only: bool = False) -> list[dict[str, Any]]: ...

    def record_hot_cohort_revision(
        self, value: dict[str, Any], current: dict[str, Any] | None = None,
    ) -> bool: ...

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]: ...

    def append_upper_limit_facts(self, values: list[dict[str, Any]]) -> int: ...

    def append_vi_events(self, values: list[dict[str, Any]]) -> int: ...


@dataclass(frozen=True, slots=True)
class _PendingLimitFact:
    key: tuple[str, str, str]
    payload: bytes
    completion: asyncio.Future[None]
    origin: tuple[str, str, str, str]
    trace_token: str | None


@dataclass
class _ConditionRegistration:
    selected: tuple[str, str]
    revision: int
    signals: list[tuple[str, str, str, str]] = field(default_factory=list)
    initial: list[tuple[str, str, str, str]] = field(default_factory=list)
    cursors: set[str] = field(default_factory=set)


def select_condition(
    values: list[tuple[str, str]], *, exact_name: str = "", substring: str = "15%",
) -> tuple[tuple[str, str] | None, str]:
    """이름 기준으로만 고른다. 후보가 0개나 복수면 추측하지 않는다."""
    if exact_name:
        exact = [value for value in values if value[1] == exact_name]
        if len(exact) == 1:
            return exact[0], "EXACT"
        return None, "EXACT_NOT_FOUND" if not exact else "EXACT_AMBIGUOUS"
    matches = [value for value in values if substring and substring in value[1]]
    if len(matches) == 1:
        return matches[0], "UNIQUE_SUBSTRING"
    return None, "NO_MATCH" if not matches else "AMBIGUOUS"


class MarketEventService:
    """조건 선택 정책, cohort 수명, VI/상한가 사실을 소유한다."""

    def __init__(
        self, broker: CentralRestBroker, hub: RealtimeHub, store: MarketEventStore, *,
        exact_condition_name: str = "", condition_substring: str = "15%",
        condition_enabled: bool = True,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._broker = broker
        self._hub = hub
        self._store = store
        self._exact_name = exact_condition_name.strip()
        self._substring = condition_substring.strip() or "15%"
        self._condition_enabled = condition_enabled
        self._condition_revision = 0
        self._requested_revision = -1
        self._active_condition_revision = -1
        self._condition_phase = "WAITING_CONNECTION"
        self._list_policy: tuple[int, str, str] | None = None
        self._pending_condition: _ConditionRegistration | None = None
        self._clearing: set[str] = set()
        self._retry_after_clear = False
        self._condition_deadline = 0.0
        self._now = now_provider
        self._subscriber: RealtimeSubscriber | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._background: set[asyncio.Task[None]] = set()
        self._vi_inflight: dict[str, asyncio.Future[bool]] = {}
        self._state_lock = asyncio.Lock()
        self._metadata_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2000)
        self._signal_queue: asyncio.Queue[tuple[str, str, str, str, tuple[str, str] | None]] = asyncio.Queue(maxsize=5000)
        self._cohort: dict[str, dict[str, Any]] = {}
        self._upper_limits: dict[str, int] = {}
        self._last_ticks: dict[tuple[str, str], TradeTick] = {}
        self._last_tick_sessions: dict[tuple[str, str], str] = {}
        self._facts: set[tuple[str, str, str]] = set()
        # Safety envelope, not an RSS budget or a throughput guarantee.
        self._fact_capacity = 20_000
        self._fact_payload_limit = 64 * 1024 * 1024
        self._fact_retry_seconds = 1.0
        self._pending_facts: dict[tuple[str, str, str], _PendingLimitFact] = {}
        self._fact_payload_bytes = 0
        self._fact_wake = asyncio.Event()
        self._fact_space = asyncio.Event()
        self._fact_admission_lock = asyncio.Lock()
        self._fact_worker: asyncio.Task[None] | None = None
        self._fact_native: asyncio.Task[Any] | None = None
        self._fact_stop_requested = False
        self._fact_admission_closed = False
        self._event_draining = False
        self._close_task: asyncio.Task[None] | None = None
        self._fact_failures = 0
        self._fact_last_error: str | None = None
        self._fact_saturations = 0
        self._fact_high_water = 0
        self._fact_bytes_high_water = 0
        self._fact_dropped_events = 0
        self._selected: tuple[str, str] | None = None
        self._observed_sessions: set[str] = set()

    async def start(self) -> None:
        if self._tasks:
            return
        if self._close_task is not None:
            raise RuntimeError("closed_market_event_service_requires_new_instance")
        rows = await asyncio.to_thread(self._store.load_hot_cohort, active_only=True)
        self._cohort = {str(row["stock_code"]): row for row in rows}
        self._subscriber = self._hub.connect()
        await self._update_subscription()
        self._tasks = [
            asyncio.create_task(self._event_loop(), name="hot-cohort-events"),
            asyncio.create_task(self._metadata_loop(), name="hot-cohort-metadata"),
            asyncio.create_task(self._signal_loop(), name="hot-cohort-signals"),
        ]
        for code in self._cohort:
            self._queue_metadata(code)

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = owned_create_task(self._close(), name="hot-cohort-close", shutdown=True)
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._close_task)
                break
            except asyncio.CancelledError:
                if self._close_task.cancelled():
                    raise
                # A cancelled caller does not abandon an actual COMMIT or RAM facts.
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self) -> None:
        errors: list[BaseException] = []
        subscriber = self._subscriber
        self._event_draining = True
        if subscriber is not None:
            self._remember_fact_drops(subscriber)
            self._hub.disconnect(subscriber)
            # Wake an empty consumer without changing shared hub join semantics.
            if subscriber.queue.empty():
                subscriber.queue.put_nowait({"type": "hot-cohort-close"})
        event_tasks = [task for task in self._tasks
                       if getattr(task.get_coro(), "__name__", "") == "_event_loop"]
        try:
            for task in event_tasks:
                outcome = await asyncio.gather(task, return_exceptions=True)
                errors.extend(value for value in outcome if isinstance(value, Exception))
            if subscriber is not None:
                while not subscriber.queue.empty():
                    try:
                        await self._process_event(subscriber.queue.get_nowait())
                    except Exception as error:
                        errors.append(error)
            if self._tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(self._metadata_queue.join(), self._signal_queue.join()), timeout=10,
                    )
                except TimeoutError:
                    logger.warning("hot cohort 메타데이터 종료 대기가 시간 제한을 넘었습니다")
        finally:
            for task in self._tasks:
                if task not in event_tasks:
                    task.cancel()
            outcomes = await asyncio.gather(*self._tasks, return_exceptions=True)
            errors.extend(value for value in outcomes if isinstance(value, Exception) and value not in errors)
            self._tasks.clear()
            while self._background:
                await asyncio.gather(*tuple(self._background), return_exceptions=True)
            self._background.clear()
            async with self._fact_admission_lock:
                self._fact_admission_closed = True
            self._fact_stop_requested = True
            self._fact_wake.set()
            try:
                if self._fact_worker is not None:
                    await asyncio.shield(self._fact_worker)
                if self._pending_facts or self._fact_native is not None:
                    raise RuntimeError("market_event_facts_not_drained")
            finally:
                self._subscriber = None
        if errors:
            raise errors[0]

    async def on_ws_connected(self, websocket: Any) -> None:
        """연결마다 목록부터 다시 받아 seq 변경을 안전하게 반영한다."""
        self._selected = None
        self._active_condition_revision = -1
        self._pending_condition = None; self._list_policy = None; self._clearing.clear()
        self._retry_after_clear = False; self._condition_deadline = 0
        self._requested_revision = -1
        await self.poll_condition_updates(websocket)
        self._spawn(self._backfill_vi(), "vi-startup-backfill")

    def update_operational_settings(self, *, enabled: bool, exact_name: str, substring: str) -> None:
        self._condition_enabled = enabled
        self._exact_name, self._substring = exact_name.strip(), substring.strip()
        self._condition_revision += 1
        self._condition_phase = "WAITING_LIST" if enabled else "WAITING_CLEAR"

    def condition_status(self) -> dict[str, Any]:
        return {"apply_status": self._condition_phase, "policy_revision": self._condition_revision,
            "enabled": self._condition_enabled, "active": list(self._selected) if self._selected else None,
            "active_policy_revision": self._active_condition_revision,
            "configured_exact_name": self._exact_name, "configured_substring": self._substring,
            "coverage": "KRX", "fact_collection": self._fact_collection_status()}

    def _remember_fact_drops(self, subscriber: RealtimeSubscriber) -> None:
        self._fact_dropped_events = max(self._fact_dropped_events, subscriber.dropped_events)

    def _fact_collection_status(self) -> dict[str, Any]:
        if self._subscriber is not None:
            self._remember_fact_drops(self._subscriber)
        return {"scope": "current_process_observed_cohort_only",
            "durability": "database_ack_only", "restart_coverage": "unverified",
            "recovery_required": bool(self._fact_dropped_events or self._fact_last_error or self._fact_saturations),
            "coverage": "partial" if self._fact_dropped_events else
                        "verification_required" if self._fact_last_error or self._fact_saturations else "observed_only",
            "state": "retrying" if self._fact_last_error else
                     "pending" if self._pending_facts else "idle",
            "pending": len(self._pending_facts), "payload_bytes": self._fact_payload_bytes,
            "capacity": self._fact_capacity, "payload_limit_bytes": self._fact_payload_limit,
            "high_water": self._fact_high_water, "payload_high_water_bytes": self._fact_bytes_high_water,
            "saturation_count": self._fact_saturations, "save_failures": self._fact_failures,
            "last_error_type": self._fact_last_error, "dropped_events": self._fact_dropped_events,
            "native_inflight": self._fact_native is not None}

    async def poll_condition_updates(self, websocket: Any) -> None:
        now = asyncio.get_running_loop().time()
        if self._condition_deadline and now >= self._condition_deadline:
            self._list_policy = None
            await self._fail_registration(websocket)
        if self._clearing:
            if self._requested_revision != self._condition_revision:
                self._retry_after_clear = True
                self._requested_revision = self._condition_revision
                for seq in tuple(self._clearing):
                    await websocket.send(json.dumps({"trnm": "CNSRCLR", "seq": seq}))
                self._condition_deadline = now + 15
            return
        if self._list_policy is not None or self._pending_condition is not None:
            return
        if self._requested_revision == self._condition_revision:
            return
        self._requested_revision = self._condition_revision
        if not self._condition_enabled:
            if self._selected:
                await self._clear_condition(self._selected[0], websocket)
            else:
                self._condition_phase = "OFF"
                self._active_condition_revision = self._condition_revision
            return
        self._list_policy = (self._condition_revision, self._exact_name, self._substring)
        self._condition_phase = "WAITING_LIST"
        self._condition_deadline = now + 15
        await websocket.send(json.dumps({"trnm": "CNSRLST"}))

    async def _clear_condition(self, seq, websocket) -> None:
        self._clearing.add(seq)
        self._condition_phase = "WAITING_CLEAR"
        self._condition_deadline = asyncio.get_running_loop().time() + 15
        await websocket.send(json.dumps({"trnm": "CNSRCLR", "seq": seq}))

    async def _fail_registration(self, websocket) -> None:
        pending, self._pending_condition = self._pending_condition, None
        self._condition_deadline = 0
        if pending and (self._selected is None or pending.selected[0] != self._selected[0]):
            await self._clear_condition(pending.selected[0], websocket)
        self._condition_phase = "RECOVERY_REQUIRED"

    async def handle_ws_message(self, message: dict[str, Any], websocket: Any) -> bool:
        trnm = str(message.get("trnm", "")).upper()
        if trnm == "CNSRLST":
            await self._condition_list(message, websocket)
            return True
        if trnm == "CNSRREQ":
            await self._condition_initial(message, websocket)
            return True
        if trnm == "CNSRCLR":
            seq = str(message.get("seq", "")).strip()
            if seq in self._clearing:
                self._condition_deadline = 0
                if type(message.get("return_code")) is not bool and message.get("return_code") in (0, "0"):
                    self._clearing.discard(seq)
                    if self._selected and self._selected[0] == seq:
                        self._selected = None
                    if not self._clearing:
                        if not self._condition_enabled and self._selected is None:
                            self._active_condition_revision = self._condition_revision
                            self._condition_phase = "OFF"
                        elif self._retry_after_clear:
                            self._retry_after_clear = False
                            self._requested_revision = -1
                            self._condition_phase = "WAITING_LIST"
                        else:
                            self._condition_phase = "ACTIVE" if self._active_condition_revision == self._condition_revision else "RECOVERY_REQUIRED"
                else:
                    self._condition_phase = "RECOVERY_REQUIRED"
            return True
        if trnm != "REAL":
            return False
        vi_events = parse_vi_events(message)
        references = parse_stock_price_references(message)
        for reference in references:
            if reference.code in self._cohort and reference.upper_limit_price is not None:
                self._upper_limits[reference.code] = reference.upper_limit_price
        if vi_events:
            self._spawn(
                self._save_live_vi_events([self._vi_value(value) for value in vi_events]),
                "vi-live-store",
            )
        handled = bool(vi_events or references)
        for entry in message.get("data") or ():
            if not isinstance(entry, dict) or entry.get("type") != "02":
                continue
            values = entry.get("values")
            if not isinstance(values, dict):
                continue
            handled = True
            seq = str(values.get("841", "")).strip()
            pending = self._pending_condition
            if not self._condition_enabled:
                continue
            if pending and seq == pending.selected[0] and pending.revision == self._condition_revision:
                code = _stock_code(values.get("9001") or entry.get("item"))
                signal = str(values.get("843", "")).strip().upper()
                if code and signal in {"I", "D"}:
                    if len(pending.signals) + len(pending.initial) >= 5000:
                        await self._fail_registration(websocket)
                    else:
                        pending.signals.append((code, signal, "REAL", ""))
                continue
            if self._selected is None or seq != self._selected[0]:
                continue
            code = _stock_code(values.get("9001") or entry.get("item"))
            signal = str(values.get("843", "")).strip().upper()
            if code and signal in {"I", "D"}:
                self._queue_signal(code, signal, "REAL", "")
        return handled

    async def record_condition_signal(self, code: str, signal: str, *, source: str,
                                      stock_name: str = "", condition: tuple[str, str] | None = None) -> None:
        # Preserve the original invocation time/condition before any lock wait.
        now = self._now()
        timestamp = now.timestamp()
        session_id = now.date().isoformat()
        selected_seq, selected_name = condition or self._selected or ("", "")
        # Eligibility/expiry writers already own this lock. Keep the snapshot,
        # native ACK and RAM publication under the same owner so an older
        # metadata snapshot cannot restore the previous signal after this write.
        async with self._state_lock:
            previous = self._cohort.get(code)
            is_new = previous is None or not bool(previous.get("active"))
            first_seen = timestamp if is_new else float(previous["first_seen_at"])
            entry_session = session_id if is_new else str(previous["entry_session"])
            current = {
                "stock_code": code, "stock_name": stock_name or str((previous or {}).get("stock_name", "")),
                "condition_name": selected_name, "first_seen_at": first_seen,
                "entry_session": entry_session, "last_signal": signal, "last_signal_at": timestamp,
                # D는 조건식 현재 결과에서 빠졌다는 사실일 뿐 cohort 만료가 아니다.
                "active": True, "nxt_eligible": (previous or {}).get("nxt_eligible"), "expired_at": None,
                "coverage": "KRX_CONDITION; KRX_AND_NXT_AFTER_ELIGIBILITY",
            }
            revision = self._revision(code, "ENTERED" if is_new else signal, selected_name, selected_seq,
                                      session_id, timestamp, {"source": source, "signal": signal})
            await asyncio.to_thread(self._store.record_hot_cohort_revision, revision, current)
            self._cohort[code] = current
            await self._update_subscription()
            if is_new:
                self._queue_metadata(code)

    def mark_krx_session_observed(self, session_id: str) -> None:
        if session_id in self._observed_sessions:
            return
        self._observed_sessions.add(session_id)
        now = self._now().timestamp()
        self._spawn(asyncio.to_thread(
            self._store.upsert_documents, "market_event_sessions", [{
                "owner": "krx", "key": session_id,
                "document": {"session_id": session_id, "observed": True, "first_observed_at": now},
            }],
        ), "krx-session-observed")

    async def close_krx_session(self, session_id: str) -> None:
        """이전 호출자 호환용: 정규장 종가와 전체 관측일 종료를 함께 처리한다."""
        await self.close_krx_regular_session(session_id)
        await self.close_observation_day(session_id)

    async def close_krx_regular_session(self, session_id: str) -> None:
        """15:30 KRX 정규장 종가 사실만 확정하고 cohort는 유지한다."""
        if session_id not in self._observed_sessions:
            return
        now = self._now().timestamp()
        async with self._state_lock:
            for code in tuple(self._cohort):
                tick = self._last_ticks.get((code, "KRX"))
                if tick is None:
                    # 기존 fixture/호출자가 code 단일 key로 넣은 값은 KRX로 해석한다.
                    tick = self._last_ticks.get(code)  # type: ignore[arg-type]
                tick_session = self._last_tick_sessions.get((code, "KRX"), session_id)
                trade_time = str(tick.trade_time or "") if tick is not None else ""
                if (
                    tick is not None
                    and tick_session == session_id
                    and trade_time.isdigit()
                    and trade_time.zfill(6) <= "153000"
                    and self._upper_limits.get(code) == tick.current_price
                    and _price_basis_matches_limit(tick, self._upper_limits.get(code))
                ):
                    await self._append_limit_fact(
                        code, session_id, "CLOSED_AT_LIMIT", tick, "observed-krx-regular-close",
                    )
        await asyncio.to_thread(self._store.upsert_documents, "market_event_sessions", [{
            "owner": "krx", "key": session_id,
            "document": {"session_id": session_id, "observed": True, "regular_closed_at": now},
        }])

    async def close_observation_day(self, session_id: str) -> None:
        """최종 지원 거래 종료에 다음 관측일 cohort를 한 번 만료한다."""
        if session_id not in self._observed_sessions:
            return
        now = self._now().timestamp()
        async with self._state_lock:
            for code, current in tuple(self._cohort.items()):
                if str(current["entry_session"]) == session_id:
                    continue
                expired = dict(current, active=False, expired_at=now, last_signal="EXPIRED", last_signal_at=now)
                revision = self._revision(code, "EXPIRED", str(current.get("condition_name", "")), "",
                                          session_id, now, {"basis": "next_observed_krx_session_close"})
                await asyncio.to_thread(self._store.record_hot_cohort_revision, revision, expired)
                self._cohort.pop(code, None)
        await asyncio.to_thread(self._store.upsert_documents, "market_event_sessions", [{
            "owner": "krx", "key": session_id,
            "document": {"session_id": session_id, "observed": True, "full_day_closed_at": now},
        }])
        await self._update_subscription()

    async def _condition_list(self, message: dict[str, Any], websocket: Any) -> None:
        policy, self._list_policy = self._list_policy, None
        self._condition_deadline = 0
        if policy is None or policy[0] != self._condition_revision:
            return
        if type(message.get("return_code")) is bool or message.get("return_code") not in (None, 0, "0"):
            self._condition_phase = "RECOVERY_REQUIRED"
            return
        if message.get("data") is not None and not isinstance(message["data"], (list, tuple)):
            self._condition_phase = "RECOVERY_REQUIRED"
            return
        pairs: list[tuple[str, str]] = []
        for item in message.get("data") or ():
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                seq, name = str(item[0] or "").strip(), str(item[1] or "").strip()
                if seq and name:
                    pairs.append((seq, name))
            elif isinstance(item, dict):
                seq = str(item.get("seq", item.get("condition_seq", ""))).strip()
                name = str(item.get("name", item.get("condition_name", ""))).strip()
                if seq and name:
                    pairs.append((seq, name))
        selected, status = select_condition(pairs, exact_name=policy[1], substring=policy[2])
        if selected is None:
            self._condition_phase = "RECOVERY_REQUIRED"
        elif selected == self._selected:
            self._active_condition_revision = policy[0]
            self._condition_phase = "ACTIVE"
        else:
            self._pending_condition = _ConditionRegistration(selected, policy[0])
            self._condition_phase = "WAITING_REGISTER"
            self._condition_deadline = asyncio.get_running_loop().time() + 15
            await websocket.send(json.dumps({
                "trnm": "CNSRREQ", "seq": selected[0], "search_type": "1", "stex_tp": "K",
            }, ensure_ascii=False))
        await asyncio.to_thread(self._store.upsert_documents, "condition_search_status", [{
            "owner": "hot_cohort", "key": "current", "document": {
                "status": status, "configured_exact_name": self._exact_name,
                "configured_substring": self._substring, "matches": [list(value) for value in pairs],
                "selected": list(selected) if selected else None, "coverage": "KRX",
                "runtime": self.condition_status(),
                "updated_at": self._now().timestamp(),
            },
        }])

    async def _condition_initial(self, message: dict[str, Any], websocket: Any) -> None:
        pending = self._pending_condition
        if pending is None:
            return
        seq = str(message.get("seq", "")).strip()
        # Initial legacy frames may omit seq; after a runtime change this is ambiguous.
        if (seq and seq != pending.selected[0]) or (not seq and pending.revision > 0):
            return
        if pending.revision != self._condition_revision or type(message.get("return_code")) is bool or message.get("return_code") not in (None, 0, "0"):
            await self._fail_registration(websocket)
            return
        data = message.get("data")
        if data is not None and not isinstance(data, (list, tuple)):
            await self._fail_registration(websocket)
            return
        if data is None and message.get("return_code") is None:
            return
        for item in message.get("data") or ():
            if not isinstance(item, dict):
                continue
            code = _stock_code(item.get("jmcode") or item.get("stk_cd") or item.get("9001"))
            if code:
                pending.initial.append((code, "I", "INITIAL", str(item.get("stk_nm") or item.get("302") or "")))
        if len(pending.signals) + len(pending.initial) > 5000:
            await self._fail_registration(websocket)
            return
        cont_yn = str(message.get("cont_yn", "N")).upper()
        next_key = str(message.get("next_key", "")).strip()
        if cont_yn not in {"N", "Y"}:
            await self._fail_registration(websocket)
            return
        if cont_yn == "Y":
            if not next_key or next_key in pending.cursors:
                await self._fail_registration(websocket)
                return
            pending.cursors.add(next_key)
            await websocket.send(json.dumps({
                "trnm": "CNSRREQ", "seq": pending.selected[0], "search_type": "1", "stex_tp": "K",
                "cont_yn": "Y", "next_key": next_key,
            }, ensure_ascii=False))
            self._condition_deadline = asyncio.get_running_loop().time() + 15
            return
        if self._signal_queue.qsize() + len(pending.signals) + len(pending.initial) > self._signal_queue.maxsize:
            await self._fail_registration(websocket)
            return
        previous = self._selected
        self._selected = pending.selected
        self._active_condition_revision = pending.revision
        self._pending_condition = None
        self._condition_deadline = 0
        self._condition_phase = "ACTIVE"
        for code, signal, source, name in pending.initial + pending.signals:
            self._queue_signal(code, signal, source, name, condition=pending.selected)
        if previous and previous[0] != pending.selected[0]:
            await self._clear_condition(previous[0], websocket)

    async def _update_subscription(self) -> None:
        if self._subscriber is None:
            return
        codes = sorted(self._cohort)
        nxt = sorted(code for code, value in self._cohort.items() if value.get("nxt_eligible") is True)
        self._hub.update_subscription(self._subscriber, codes, nxt, program_codes=[])

    def _queue_metadata(self, code: str) -> None:
        try:
            self._metadata_queue.put_nowait(code)
        except asyncio.QueueFull:
            logger.warning("hot cohort 메타데이터 대기열이 가득 찼습니다: %s", code)

    def _queue_signal(self, code: str, signal: str, source: str, stock_name: str,
                      *, condition: tuple[str, str] | None = None) -> None:
        try:
            self._signal_queue.put_nowait((code, signal, source, stock_name, condition or self._selected))
        except asyncio.QueueFull:
            logger.error("hot cohort 조건 신호 대기열이 가득 찼습니다: %s %s", code, signal)

    async def _signal_loop(self) -> None:
        while True:
            code, signal, source, stock_name, condition = await self._signal_queue.get()
            try:
                await self.record_condition_signal(code, signal, source=source, stock_name=stock_name,
                                                   condition=condition)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("hot cohort 조건 신호 저장 실패(수집 계속): %s %s", code, error)
            finally:
                self._signal_queue.task_done()

    async def _metadata_loop(self) -> None:
        while True:
            code = await self._metadata_queue.get()
            try:
                await self._load_metadata(code)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("hot cohort 종목 메타데이터 조회 실패(수집 계속): %s %s", code, error)
            finally:
                self._metadata_queue.task_done()

    async def _load_metadata(self, code: str) -> None:
        rows = await asyncio.to_thread(self._store.load_documents, "stock_nxt_eligibility", code, 1)
        payload = _nested_payload(rows)
        if not payload:
            payload = (await self._broker.request("ka10100", "/api/dostk/stkinfo", {"stk_cd": code})).payload
        eligible = str(payload.get("nxtEnable", payload.get("nxt_enable", ""))).upper() == "Y"
        async with self._state_lock:
            if code in self._cohort:
                self._cohort[code]["nxt_eligible"] = eligible
                current = self._cohort[code]
                now = self._now().timestamp()
                revision = self._revision(code, "ELIGIBILITY", str(current.get("condition_name", "")), "",
                                          str(current["entry_session"]), now, {"nxt_eligible": eligible})
                await asyncio.to_thread(self._store.record_hot_cohort_revision, revision, current)
                await self._update_subscription()
        rows = await asyncio.to_thread(
            self._store.load_documents, "stock_price_references", code, 1,
        )
        reference = rows[0].get("document", {}) if rows else {}
        price = (
            _positive_int(reference.get("upper_limit_price"))
            if isinstance(reference, dict) else None
        )
        rows = await asyncio.to_thread(self._store.load_documents, "stock_fundamentals", code, 1)
        payload = _nested_payload(rows)
        if price is None:
            price = _positive_int(payload.get("upl_pric")) if payload else None
        if price is None:
            payload = (await self._broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": code})).payload
            price = _positive_int(payload.get("upl_pric"))
        if price is not None:
            self._upper_limits[code] = price

    async def _event_loop(self) -> None:
        while True:
            subscriber = self._subscriber
            if subscriber is None:
                if self._event_draining:
                    return
                await asyncio.sleep(0.1)
                continue
            self._remember_fact_drops(subscriber)
            if self._event_draining and subscriber.queue.empty():
                return
            event = await subscriber.queue.get()
            await self._process_event(event)

    async def _process_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "stock_reference" and isinstance(event.get("payload"), dict):
            allowed = StockPriceReference.__dataclass_fields__.keys()
            try:
                reference = StockPriceReference(**{
                    key: value for key, value in event["payload"].items() if key in allowed
                })
            except TypeError:
                return
            if reference.code in self._cohort and reference.upper_limit_price is not None:
                self._upper_limits[reference.code] = reference.upper_limit_price
            return
        if event.get("type") != "trade" or not isinstance(event.get("payload"), dict):
            return
        allowed = TradeTick.__dataclass_fields__.keys()
        try:
            tick = TradeTick(**{key: value for key, value in event["payload"].items() if key in allowed})
        except TypeError:
            return
        if tick.code not in self._cohort:
            return
        self._last_ticks[(tick.code, str(tick.market or "KRX").upper())] = tick
        self._last_tick_sessions[(tick.code, str(tick.market or "KRX").upper())] = self._now().date().isoformat()
        await self._admit_trade(tick)

    async def observe_trade(self, tick: TradeTick) -> None:
        for completion in await self._admit_trade(tick):
            await asyncio.shield(completion)

    async def _admit_trade(self, tick: TradeTick) -> list[asyncio.Future[None]]:
        async with self._fact_admission_lock:
            session = self._now().date().isoformat()
            upper = self._upper_limits.get(tick.code)
            # Do not mix a new price basis with an old session's upper limit.
            if upper is None or not _price_basis_matches_limit(tick, upper):
                return []
            statuses = []
            if (tick.high_price or 0) >= upper or (tick.current_price or 0) >= upper:
                statuses.append(("TOUCHED", "0B-high-or-current"))
            elif (tick.high_price or 0) < upper and (tick.current_price or 0) < upper:
                statuses.append(("UNKNOWN", "tracking-started-after-session-open"))
            if tick.current_price == upper:
                statuses.append(("CURRENT", "0B-current"))
            # Freeze the entire decision before capacity waits can cross midnight
            # or before another task changes reference prices.
            plans = [self._prepare_limit_fact(tick.code, session, status, tick, evidence)
                     for status, evidence in statuses]
            completions = []
            for plan in plans:
                if plan is not None:
                    completions.append(await self._admit_limit_fact(plan))
            return completions

    async def _append_limit_fact(self, code: str, session: str, status: str,
                                 tick: TradeTick, evidence: str) -> None:
        async with self._fact_admission_lock:
            plan = self._prepare_limit_fact(code, session, status, tick, evidence)
            completion = await self._admit_limit_fact(plan) if plan is not None else None
        if completion is not None:
            await asyncio.shield(completion)

    def _prepare_limit_fact(self, code: str, session: str, status: str,
                            tick: TradeTick, evidence: str) -> _PendingLimitFact | None:
        if self._fact_admission_closed:
            raise RuntimeError("market_event_fact_admission_closed")
        fact_tuple = (code, session, status)
        if fact_tuple in self._facts:
            return None
        pending = self._pending_facts.get(fact_tuple)
        if pending is not None:
            return pending
        now = self._now().timestamp()
        document = {
            "stock_code": code, "session_id": session, "status": status,
            "upper_limit_price": self._upper_limits.get(code), "current_price": tick.current_price,
            "high_price": tick.high_price, "effective_at": now, "available_at": time.time(),
            "source": "kiwoom-websocket-0B", "evidence": evidence,
        }
        key = _hash([code, session, status, document["upper_limit_price"]])
        document.update({"fact_key": key, "fact_id": str(uuid.uuid5(uuid.NAMESPACE_URL, key))})
        identity = operation_identity()
        origin = tuple(identity[name] for name in
                       ("workload_id", "producer_component", "actor_id", "cause_input_id"))
        if any(type(value) is not str or len(value) > 160 for value in origin):
            origin = ("", "", "", "")
        return _PendingLimitFact(fact_tuple,
            json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            asyncio.get_running_loop().create_future(), origin, input_token("store_inputs"))

    async def _admit_limit_fact(self, plan: _PendingLimitFact) -> asyncio.Future[None]:
        if len(plan.payload) > self._fact_payload_limit:
            raise RuntimeError("upper_limit_fact_exceeds_pending_payload_limit")
        while True:
            if plan.key in self._facts:
                if not plan.completion.done():
                    plan.completion.set_result(None)
                return plan.completion
            existing = self._pending_facts.get(plan.key)
            if existing is not None:
                return existing.completion
            if (len(self._pending_facts) < self._fact_capacity and
                    self._fact_payload_bytes + len(plan.payload) <= self._fact_payload_limit):
                break
            self._fact_saturations += 1
            self._fact_space.clear()
            await self._fact_space.wait()
        if self._fact_worker is None or self._fact_worker.done():
            self._fact_worker = owned_create_task(self._save_limit_facts(), name="hot-cohort-facts",
                                                   shutdown=self._event_draining)
        self._pending_facts[plan.key] = plan
        self._fact_payload_bytes += len(plan.payload)
        self._fact_high_water = max(self._fact_high_water, len(self._pending_facts))
        self._fact_bytes_high_water = max(self._fact_bytes_high_water, self._fact_payload_bytes)
        self._fact_wake.set()
        return plan.completion

    async def _save_limit_facts(self) -> None:
        while True:
            if not self._pending_facts:
                if self._fact_stop_requested:
                    return
                self._fact_wake.clear()
                await self._fact_wake.wait()
                continue
            plan = next(iter(self._pending_facts.values()))
            document = json.loads(plan.payload)
            workload, component, actor, cause = plan.origin
            if not all((workload, component, actor)):
                workload, component = "market_events", f"hot-cohort:{id(self):x}"
                actor = component + ":facts"
                cause = ""
            if plan.trace_token != input_token("store_inputs"):
                cause = ""
            try:
                with capture_owner(workload, component, actor, cause_input_id=cause):
                    self._fact_native = owned_create_task(owned_to_thread(
                        self._store.append_upper_limit_facts, [dict(document, document=document)]),
                        name="hot-cohort-fact-commit", shutdown=self._event_draining or self._fact_stop_requested)
                try:
                    while True:
                        try:
                            await asyncio.shield(self._fact_native)
                            break
                        except asyncio.CancelledError:
                            if self._fact_native.cancelled():
                                raise
                            # Only our real executor completion releases this fact.
                            self._fact_stop_requested = True
                finally:
                    self._fact_native = None
                self._pending_facts.pop(plan.key)
                self._fact_payload_bytes -= len(plan.payload)
                self._facts.add(plan.key)
                plan.completion.set_result(None)
                self._fact_space.set()
                self._fact_last_error = None
            except Exception as error:
                self._fact_failures += 1
                if self._fact_last_error != type(error).__name__:
                    logger.warning("상한가 사실 저장 실패(입력 보존, 재시도): %s", type(error).__name__)
                self._fact_last_error = type(error).__name__
                await asyncio.sleep(self._fact_retry_seconds)

    async def _backfill_vi(self) -> None:
        body = {
            "mrkt_tp": "000", "bf_mkrt_tp": "0", "stk_cd": "", "motn_tp": "0",
            "skip_stk": "000000000", "trde_qty_tp": "0", "min_trde_qty": "0", "max_trde_qty": "0",
            "trde_prica_tp": "0", "min_trde_prica": "0", "max_trde_prica": "0",
            "motn_drc": "0", "stex_tp": "3",
        }
        try:
            cont_yn, next_key = "N", ""
            for _ in range(10):
                result = await self._broker.request(
                    "ka10054", "/api/dostk/stkinfo", body, cont_yn=cont_yn, next_key=next_key,
                )
                rows = result.payload.get("motn_stk", [])
                values = ([self._backfill_vi_value(row) for row in rows if isinstance(row, dict)]
                          if isinstance(rows, list) else [])
                if values:
                    await asyncio.to_thread(self._store.append_vi_events, values)
                if not result.has_next or not result.next_key:
                    break
                cont_yn, next_key = "Y", result.next_key
        except Exception as error:
            logger.warning("VI 누락 보완 실패(실시간 수집 계속): %s", error)

    async def _save_live_vi_events(self, values: list[dict[str, Any]]) -> None:
        # DB와 같은 불변 event_key만 합친다. 완료된 키는 캐시하지 않는다.
        remaining = {}
        for value in values:
            remaining.setdefault(value["event_key"], value)
        while remaining:
            waiting = {key: self._vi_inflight[key] for key in remaining if key in self._vi_inflight}
            if not waiting:
                break
            outcomes = await asyncio.gather(*(asyncio.shield(future) for future in waiting.values()))
            for key, committed in zip(waiting, outcomes):
                if committed:
                    remaining.pop(key)
            # 실패한 선행 저장의 중복은 버리지 않고 기존처럼 다시 저장한다.
        if not remaining:
            return
        completion = asyncio.get_running_loop().create_future()
        for key in remaining:
            self._vi_inflight[key] = completion
        save = asyncio.create_task(asyncio.to_thread(self._store.append_vi_events, list(remaining.values())))
        cancelled = False
        try:
            while True:
                try:
                    await asyncio.shield(save)
                    break
                except asyncio.CancelledError:
                    if save.cancelled():
                        raise
                    # to_thread의 취소는 실제 COMMIT을 멈추지 않는다.
                    # thread가 끝날 때까지 소유권과 종료 대기를 유지한다.
                    cancelled = True
        finally:
            committed = save.done() and not save.cancelled() and save.exception() is None
            for key in remaining:
                if self._vi_inflight.get(key) is completion:
                    self._vi_inflight.pop(key)
            completion.set_result(committed)
        if cancelled:
            raise asyncio.CancelledError

    def _spawn(self, coroutine: Any, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._background.add(task)
        task.add_done_callback(self._background_done)

    def _background_done(self, task: asyncio.Task[None]) -> None:
        self._background.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("시장 이벤트 백그라운드 저장 실패(수집 계속): %s", error)

    def _vi_value(self, event: ViEvent) -> dict[str, Any]:
        now = self._now()
        effective = _effective_text(now, event.trigger_time or event.release_time)
        document = dict(asdict(event), effective_at=effective, received_at=time.time(), source="kiwoom-websocket-1h")
        key = _hash([now.date().isoformat(), event.code, event.event_kind, event.vi_type, event.trigger_price,
                     event.trigger_time, event.release_time, event.trigger_count, event.exchange])
        return dict(document, stock_code=event.code, price=event.trigger_price, event_key=key,
                    event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)), available_at=time.time(), document=document)

    def _backfill_vi_value(self, row: dict[str, Any]) -> dict[str, Any]:
        code = _stock_code(row.get("stk_cd"))
        document = {"stock_code": code, "event_kind": str(row.get("motn_tp", "UNKNOWN")),
                    "vi_type": str(row.get("vi_type", row.get("motn_cls", "UNKNOWN"))),
                    "price": _positive_int(row.get("motn_pric")), "direction": str(row.get("motn_drc", "")),
                    "trigger_count": _positive_int(row.get("motn_cnt")), "exchange": str(row.get("stex_tp", "")),
                    "effective_at": str(row.get("motn_tm", "")) or None, "received_at": time.time(),
                    "available_at": time.time(), "source": "kiwoom-rest-ka10054", "raw": row}
        key = _hash(["ka10054", code, row])
        return dict(document, event_key=key, event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    document=document)

    @staticmethod
    def _revision(code: str, event_type: str, condition_name: str, condition_seq: str,
                  session_id: str, effective_at: float, extra: dict[str, Any]) -> dict[str, Any]:
        document = {"stock_code": code, "event_type": event_type, "condition_name": condition_name,
                    "condition_seq": condition_seq, "session_id": session_id,
                    "effective_at": effective_at, "available_at": time.time(), **extra}
        key = _hash([code, event_type, condition_name, condition_seq, session_id, extra])
        return dict(document, revision_key=key, revision_id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    document=document)


def _stock_code(value: object) -> str:
    return str(value or "").strip().removeprefix("A").removesuffix("_NX").removesuffix("_AL")


def _nested_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows or not isinstance(rows[0].get("document"), dict):
        return {}
    document = rows[0]["document"]
    payload = document.get("payload", document)
    return payload if isinstance(payload, dict) else {}


def _positive_int(value: object) -> int | None:
    try:
        parsed = abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return parsed or None


def _price_basis_matches_limit(tick: TradeTick, upper_limit: int | None) -> bool:
    if upper_limit is None or tick.current_price is None or tick.current_price < upper_limit:
        return True
    return tick.change_rate is None or tick.change_rate >= 29.0


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _effective_text(now: datetime, raw_time: str | None) -> str | None:
    raw = str(raw_time or "").strip().zfill(6)
    if len(raw) == 6 and raw.isdigit():
        return f"{now.date().isoformat()}T{raw[:2]}:{raw[2:4]}:{raw[4:]}"
    return None
