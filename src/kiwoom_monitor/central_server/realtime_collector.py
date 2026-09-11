from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, time as clock_time
from typing import Any, Callable

from websockets.asyncio.client import connect

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    parse_market_index_ticks, parse_order_executions, parse_program_trade_ticks, parse_trade_ticks,
)
from kiwoom_monitor.domain.market_data_contract import MarketDataObservation
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    DataValueKind,
    ObservationOrigin,
)
from .realtime_hub import RealtimeHub
from .database import QueryStore
from .minute_bars import MinuteBarAccumulator
from .market_observations import (
    bar_observation_key,
    market_state_observation,
    minute_bar_observation,
)


WS_BASE_URLS = {
    "mock": "wss://mockapi.kiwoom.com:10000",
    "real": "wss://api.kiwoom.com:10000",
}

logger = logging.getLogger(__name__)


def _market_history_trade_time(raw_value: object, fallback: datetime) -> str:
    raw = str(raw_value or "").strip()
    candidate = raw.zfill(6)
    if raw.isdigit() and len(candidate) == 6:
        hour, minute, second = (int(candidate[:2]), int(candidate[2:4]), int(candidate[4:6]))
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
            return candidate
    return fallback.strftime("%H%M%S")


class CentralRealtimeCollector:
    """키움 WebSocket 한 연결을 소유하고 허브에 정규화 이벤트를 발행한다."""

    def __init__(
        self, token_provider: Callable[[], str], environment: str, hub: RealtimeHub,
        now_provider: Callable[[], datetime], store: QueryStore | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._environment = environment
        self._hub = hub
        self._now_provider = now_provider
        self._store = store
        self._task: asyncio.Task[None] | None = None
        self._snapshot_task: asyncio.Task[None] | None = None
        self._pending_snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending_market_history: dict[
            str,
            tuple[str, str, dict[str, Any], MarketDataObservation[dict[str, Any]]],
        ] = {}
        self._pending_minute_retries: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._minute_bars = MinuteBarAccumulator()
        self._abnormal_disconnects = 0
        self._reconnects = 0
        self._connected_once = False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="kiwoom-central-realtime")
        if self._store is not None and self._snapshot_task is None:
            self._snapshot_task = asyncio.create_task(self._save_snapshots(), name="kiwoom-realtime-snapshots")

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        snapshot_task, self._snapshot_task = self._snapshot_task, None
        if snapshot_task is not None:
            snapshot_task.cancel()
            try:
                await snapshot_task
            except asyncio.CancelledError:
                pass
        await self._flush_snapshots()

    async def _run(self) -> None:
        while True:
            codes, nxt_codes = self._hub.requested_codes()
            session = self._market_session()
            if not codes or session is None or (session == "NXT" and not nxt_codes):
                await asyncio.sleep(1)
                continue
            try:
                await self._receive(session)
                self._hub.set_upstream_ready((), False)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._hub.set_upstream_ready((), False)
                self._abnormal_disconnects += 1
                logger.warning("키움 중앙 실시간 연결 실패: %s", error)
                self._hub.publish({"type": "connection_failed", "message": str(error)})
                self._publish_diagnostics(str(error))
                await asyncio.sleep(3)

    async def _receive(self, session: str) -> None:
        token = await asyncio.to_thread(self._token_provider)
        uri = f"{WS_BASE_URLS[self._environment]}/api/dostk/websocket"
        async with connect(uri, open_timeout=15, ping_interval=None) as websocket:
            await websocket.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if login.get("return_code") not in (None, 0, "0"):
                raise RuntimeError(f"WebSocket 로그인 실패: {login.get('return_msg', '')}")
            logger.info("키움 중앙 실시간 로그인 성공: %s", session)
            subscribed: tuple[str, ...] = ()
            subscribed_nxt: tuple[str, ...] = ()
            if self._connected_once:
                self._reconnects += 1
            self._connected_once = True
            self._publish_diagnostics("")
            pending_added: tuple[str, ...] = ()
            while self._market_session() == session:
                codes, nxt_codes = self._hub.requested_codes()
                if not codes or (session == "NXT" and not nxt_codes):
                    return
                if codes != subscribed or nxt_codes != subscribed_nxt:
                    await self._send_subscription(websocket, session, codes, nxt_codes)
                    effective = nxt_codes if session == "NXT" else codes
                    previous_effective = subscribed_nxt if session == "NXT" else subscribed
                    pending_added = tuple(code for code in effective if code not in previous_effective)
                    subscribed, subscribed_nxt = codes, nxt_codes
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=1)
                except TimeoutError:
                    continue
                message = json.loads(raw)
                if str(message.get("trnm", "")).upper() == "PING":
                    await websocket.send(json.dumps(message))
                    continue
                if str(message.get("trnm", "")).upper() == "REG":
                    if message.get("return_code") not in (None, 0, "0"):
                        raise RuntimeError(f"WebSocket 구독 실패: {message.get('return_msg', '')}")
                    logger.info("키움 중앙 실시간 구독 승인: %s · %s종목", session, len(subscribed))
                    effective = subscribed_nxt if session == "NXT" else subscribed
                    self._hub.set_upstream_ready(effective, True)
                    self._hub.publish({"type": "connection_opened", "codes": list(subscribed)})
                    if pending_added:
                        self._hub.publish({"type": "codes_added", "codes": list(pending_added)})
                    self._hub.publish({"type": "subscription_ready"})
                    pending_added = ()
                    continue
                self._publish_parsed(message)

    def _publish_parsed(self, message: dict[str, Any]) -> None:
        for name, parser in (
            ("trade", parse_trade_ticks), ("order_execution", parse_order_executions),
            ("market_state", parse_market_index_ticks), ("program_trade", parse_program_trade_ticks),
        ):
            for value in parser(message):
                payload = asdict(value)
                event = {"type": name, "payload": payload}
                code = str(payload.get("code", ""))
                self._hub.publish(event, code)
                if name in {"trade", "market_state", "program_trade"}:
                    item_key = code or str(payload.get("market", ""))
                    self._pending_snapshots[(name, item_key)] = {
                        "event_type": name, "item_key": item_key,
                        "received_at": time.time(), "event": event,
                    }
                if name == "trade":
                    self._minute_bars.add(value, self._now_provider(), time.time())
                elif name == "market_state":
                    market = str(payload.get("market", ""))
                    now = self._now_provider()
                    trade_time = _market_history_trade_time(payload.get("trade_time"), now)
                    minute_key = f"{now.date().isoformat()}T{trade_time[:2]}:{trade_time[2:4]}"
                    history_value = {"market": market, "value": payload}
                    self._pending_market_history[market] = (
                        market,
                        minute_key,
                        history_value,
                        market_state_observation(market, minute_key, history_value, now),
                    )

    async def _save_snapshots(self) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                await self._flush_snapshots()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 개별 저장 실패는 _flush_snapshots가 대기 자료를 복원한다.
                # 루프까지 종료되면 이후 모든 실시간 자료가 조용히 누락되므로
                # 다음 주기에 다시 시도한다.
                logger.exception("키움 중앙 실시간 DB 저장 실패; 다음 주기에 재시도합니다")

    async def _flush_snapshots(self) -> None:
        if self._store is None:
            return
        snapshots = self._pending_snapshots
        values = list(snapshots.values())
        self._pending_snapshots.clear()
        if values:
            try:
                await asyncio.to_thread(self._store.save_realtime_snapshots, values)
            except Exception:
                for value in values:
                    key = (str(value["event_type"]), str(value["item_key"]))
                    self._pending_snapshots.setdefault(key, value)
                raise
        drained_minute_bars = self._minute_bars.drain_dirty()
        minute_bars = list(self._pending_minute_retries.values()) + drained_minute_bars
        self._pending_minute_retries.clear()
        if minute_bars:
            observations = []
            for value in minute_bars:
                observation = minute_bar_observation(
                    value,
                    origin=ObservationOrigin.REALTIME,
                    completeness=DataCompleteness.IN_PROGRESS,
                    source="kiwoom-websocket-0B",
                    value_kind=DataValueKind.ACTUAL,
                )
                observations.append((bar_observation_key(observation), observation))
            try:
                await asyncio.to_thread(
                    self._store.save_minute_bars,
                    minute_bars,
                    observations=observations,
                )
            except Exception:
                for value in minute_bars:
                    self._merge_minute_retry(value)
                raise
        market_history_by_subject = self._pending_market_history
        market_history = list(market_history_by_subject.values())
        self._pending_market_history.clear()
        for index, (subject, snapshot_key, payload, observation) in enumerate(market_history):
            try:
                await asyncio.to_thread(
                    self._store.save_dataset_snapshot,
                    "market_state",
                    subject,
                    snapshot_key,
                    payload,
                    observation=observation,
                )
            except Exception:
                for retry_value in market_history[index:]:
                    self._pending_market_history.setdefault(retry_value[0], retry_value)
                raise

    def _merge_minute_retry(self, value: dict[str, Any]) -> None:
        """저장하지 못한 증분 봉을 키별로 합쳐 다음 주기에 한 번만 재시도한다."""
        key = tuple(str(value[name]) for name in ("trading_date", "minute", "code", "market"))
        previous = self._pending_minute_retries.get(key)
        if previous is None:
            self._pending_minute_retries[key] = dict(value)
            return
        previous["high"] = max(int(previous["high"]), int(value["high"]))
        previous["low"] = min(int(previous["low"]), int(value["low"]))
        previous["close"] = int(value["close"])
        previous["volume"] = int(previous["volume"]) + int(value["volume"])
        previous["trade_value_million_won"] = (
            int(previous["trade_value_million_won"]) + int(value["trade_value_million_won"])
        )
        previous["updated_at"] = max(float(previous["updated_at"]), float(value["updated_at"]))

    async def _send_subscription(
        self, websocket: Any, session: str, codes: tuple[str, ...], nxt_codes: tuple[str, ...],
    ) -> None:
        items = tuple(f"{code}_NX" for code in nxt_codes) if session == "NXT" else codes + tuple(
            f"{code}_NX" for code in nxt_codes if code in codes
        )
        program_items = tuple(f"{code}_AL" for code in codes) if self._environment == "real" else codes
        await websocket.send(json.dumps({
            "trnm": "REG", "grp_no": "1", "refresh": "1", "data": [
                {"item": list(items), "type": ["0B"]},
                {"item": list(program_items), "type": ["0w"]},
                {"item": [""], "type": ["00"]},
                {"item": ["001", "101"], "type": ["0J", "0U"]},
            ],
        }))

    def _market_session(self) -> str | None:
        now = self._now_provider()
        if now.weekday() >= 5:
            return None
        current = now.time().replace(tzinfo=None)
        if clock_time(9) <= current < clock_time(15, 30):
            return "KRX"
        if self._environment == "real" and (clock_time(8) <= current < clock_time(9) or clock_time(15, 30) <= current < clock_time(20)):
            return "NXT"
        return None

    def _publish_diagnostics(self, reason: str) -> None:
        self._hub.publish({"type": "diagnostics", "payload": {
            "abnormal_disconnects": self._abnormal_disconnects,
            "reconnects": self._reconnects,
            "last_disconnect_reason": reason,
            "updated_at": self._now_provider().isoformat(timespec="seconds"),
        }})
