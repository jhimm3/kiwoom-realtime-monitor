"""VI market-event collection using the shared collector connection."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable, Protocol

from kiwoom_monitor.domain.time_basis import as_kst, kst_now
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import ViEvent, parse_vi_events

from .diagnostic_replay_contract import captured_workload
from .diagnostic_replay_runtime import owned_create_task
from .rest_broker import CentralRestBroker

logger = logging.getLogger(__name__)


class MarketEventStore(Protocol):
    def append_vi_events(self, values: list[dict[str, Any]]) -> int: ...


class MarketEventService:
    """Own VI facts and their existing native save/drain lifetime."""

    def __init__(self, broker: CentralRestBroker, store: MarketEventStore, *,
                 now_provider: Callable[[], datetime] = kst_now) -> None:
        self._broker = broker
        self._store = store
        self._now = lambda: as_kst(now_provider())
        self._background: set[asyncio.Task[None]] = set()
        self._vi_inflight: dict[str, asyncio.Future[bool]] = {}
        self._close_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._close_task is not None:
            raise RuntimeError("closed_market_event_service_requires_new_instance")

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = owned_create_task(self._close(), name="market-event-close", shutdown=True)
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
        while self._background:
            await asyncio.gather(*tuple(self._background), return_exceptions=True)
        self._background.clear()

    async def on_ws_connected(self, websocket: Any) -> None:
        self._spawn(self._backfill_vi(), "vi-startup-backfill")

    async def handle_ws_message(self, message: dict[str, Any], websocket: Any) -> bool:
        if str(message.get("trnm", "")).upper() != "REAL":
            return False
        events = parse_vi_events(message)
        if events:
            self._spawn(self._save_live_vi_events([self._vi_value(value) for value in events]),
                        "vi-live-store")
        return bool(events)

    @captured_workload('market_events', 'vi_backfill', actor_per_invocation=True)
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

    @captured_workload('market_events', 'vi_live_save', actor_per_invocation=True)
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
        received_at = now.timestamp()
        document = dict(asdict(event), effective_at=effective, received_at=received_at, source="kiwoom-websocket-1h")
        key = _hash([now.date().isoformat(), event.code, event.event_kind, event.vi_type, event.trigger_price,
                     event.trigger_time, event.release_time, event.trigger_count, event.exchange])
        return dict(document, stock_code=event.code, price=event.trigger_price, event_key=key,
                    event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)), available_at=received_at, document=document)

    def _backfill_vi_value(self, row: dict[str, Any]) -> dict[str, Any]:
        code = _stock_code(row.get("stk_cd"))
        received_at = self._now().timestamp()
        document = {"stock_code": code, "event_kind": str(row.get("motn_tp", "UNKNOWN")),
                    "vi_type": str(row.get("vi_type", row.get("motn_cls", "UNKNOWN"))),
                    "price": _positive_int(row.get("motn_pric")), "direction": str(row.get("motn_drc", "")),
                    "trigger_count": _positive_int(row.get("motn_cnt")), "exchange": str(row.get("stex_tp", "")),
                    "effective_at": str(row.get("motn_tm", "")) or None, "received_at": received_at,
                    "available_at": received_at, "source": "kiwoom-rest-ka10054", "raw": row}
        key = _hash(["ka10054", code, row])
        return dict(document, event_key=key, event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    document=document)


def _stock_code(value: object) -> str:
    return str(value or "").strip().removeprefix("A").removesuffix("_NX").removesuffix("_AL")

def _positive_int(value: object) -> int | None:
    try:
        parsed = abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return parsed or None

def _effective_text(now: datetime, raw_time: str | None) -> str | None:
    raw = str(raw_time or "").strip().zfill(6)
    if len(raw) == 6 and raw.isdigit():
        return f"{now.date().isoformat()}T{raw[:2]}:{raw[2:4]}:{raw[4:]}"
    return None

def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
