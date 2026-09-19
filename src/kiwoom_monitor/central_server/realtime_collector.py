from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, time as clock_time, timezone
from typing import Any, Callable

from websockets.asyncio.client import connect

from kiwoom_monitor.application.market_session_schedule import realtime_subscription_target

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    parse_account_balance_changes, parse_market_index_ticks, parse_order_executions,
    parse_program_trade_ticks, parse_stock_price_references, parse_trade_ticks,
)
from kiwoom_monitor.domain.market_data_contract import MarketDataObservation
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    DataValueKind,
    ObservationOrigin,
)
from kiwoom_monitor.domain.order_contract import AccountScope
from .realtime_hub import RealtimeHub
from .database import QueryStore
from .minute_bars import MinuteBarAccumulator, SecondTradeAccumulator
from .market_observations import (
    bar_observation_key,
    market_state_observation,
    minute_bar_observation,
)


WS_BASE_URLS = {
    "mock": "wss://mockapi.kiwoom.com:10000",
    "real": "wss://api.kiwoom.com:10000",
}

# 키움 실시간 REG의 data.item은 한 행당 최대 100개다. 중앙 서버는
# TOP20뿐 아니라 당일/익일 15% 조건 코호트도 함께 구독하므로 타입별로
# 그룹을 나눠 한 그룹의 종목 등록 한도를 넘기지 않는다.
REALTIME_ITEMS_PER_GROUP = 100
REALTIME_ITEMS_PER_TYPE = 200
REALTIME_REG_INTERVAL_SECONDS = 0.25


def _chunks(values: tuple[str, ...], size: int = REALTIME_ITEMS_PER_GROUP) -> tuple[tuple[str, ...], ...]:
    return tuple(values[index:index + size] for index in range(0, len(values), size))


def _registered_trade_sources(
    groups: dict[str, list[dict[str, object]]],
) -> set[tuple[str, str]]:
    """실제 전송한 0B item을 정규 종목·시세 출처 쌍으로 바꾼다."""
    result: set[tuple[str, str]] = set()
    for rows in groups.values():
        for row in rows:
            if row.get("type") != ["0B"]:
                continue
            for value in row.get("item", []):
                raw = str(value).strip()
                if not raw:
                    continue
                market = "NXT" if raw.endswith("_NX") else "SOR" if raw.endswith("_AL") else "KRX"
                result.add((raw.removesuffix("_NX").removesuffix("_AL"), market))
    return result


def contains_krx_observation(message: dict[str, Any]) -> bool:
    """키움의 data=null REAL 응답은 빈 관측으로 취급한다."""
    rows = message.get("data") or ()
    return any(
        isinstance(row, dict) and (
            row.get("type") in {"0J", "0U"}
            or (
                row.get("type") == "0B"
                and not str(row.get("item", "")).endswith(("_NX", "_AL"))
            )
        )
        for row in rows
    )

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
        market_events: Any | None = None,
        account_scope_resolver: Callable[[str], AccountScope | None] | None = None,
        account_event_handler: Callable[[str, object], None] | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._environment = environment
        self._hub = hub
        self._now_provider = now_provider
        self._store = store
        self._market_events = market_events
        self._account_scope_resolver = account_scope_resolver
        self._account_event_handler = account_event_handler
        self._task: asyncio.Task[None] | None = None
        self._snapshot_task: asyncio.Task[None] | None = None
        self._pending_snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending_account_entry_symbols: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending_stock_references: dict[str, dict[str, Any]] = {}
        self._pending_market_history: dict[
            str,
            tuple[str, str, dict[str, Any], MarketDataObservation[dict[str, Any]]],
        ] = {}
        self._pending_minute_retries: dict[str, dict[str, Any]] = {}
        self._pending_minute_finalizations: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._minute_bars = MinuteBarAccumulator()
        self._pending_second_retries: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._second_trades = SecondTradeAccumulator()
        self._continuous_from: dict[tuple[str, str], datetime] = {}
        self._approved_trade_sources: set[tuple[str, str]] = set()
        self._abnormal_disconnects = 0
        self._reconnects = 0
        self._connected_once = False
        self._regular_close_notified: set[str] = set()
        self._full_day_close_notified: set[str] = set()
        self._last_registration_sent_at = 0.0
        self._credential_paused = False
        self._credential_shutdown = False
        self._connection_generation = 0
        self._credential_phase = "IDLE"
        self._credential_drain_task: asyncio.Task | None = None
        self._credential_resume_task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None
        self._token_tasks: set[asyncio.Task] = set()
        self._flush_tasks: set[asyncio.Task] = set()
        self._flush_lock = asyncio.Lock()
        self._boundary_task: asyncio.Task | None = None
        self._planned_reconnect = False
        self._reconnect_deadline = 0.0
        self._reconnect_expiry_task: asyncio.Task | None = None
        self._reconnect_started_at: str | None = None
        self._reconnect_outcome = "idle"
        self._last_trade_observed_at: str | None = None
        self._last_trade_observed_clock: float | None = None
        self._reconnect_previous_trade_at: str | None = None
        self._reconnect_previous_trade_clock: float | None = None
        self._reconnect_first_trade_at: str | None = None
        self._reconnect_gap_seconds: float | None = None

    def credential_connection_status(self) -> dict[str, object]:
        codes, nxt_codes = self._hub.requested_codes()
        session = self._market_session()
        observation_expected = (session is not None and (session != "NXT" or bool(nxt_codes))
                                and (bool(codes) or (session == "KRX" and self._market_events is not None)))
        return {"generation": self._connection_generation, "phase": self._credential_phase,
                "paused": self._credential_paused, "shutdown": self._credential_shutdown,
                "planned_reconnect": self._planned_reconnect and time.monotonic() < self._reconnect_deadline,
                "remaining_seconds": max(0.0, min(30.0, self._reconnect_deadline - time.monotonic())) if self._planned_reconnect else 0.0,
                "started_at": self._reconnect_started_at, "outcome": self._reconnect_outcome,
                "observation_expected": observation_expected,
                "previous_trade_at": self._reconnect_previous_trade_at,
                "first_trade_at": self._reconnect_first_trade_at, "gap_seconds": self._reconnect_gap_seconds}

    def _publish_connection_status(self) -> None:
        self._hub.publish({"type": "connection_status", "payload": self.credential_connection_status()})

    def _start_planned_reconnect(self) -> None:
        # Fencing the same in-flight apply must not renew its finite deadline.
        if not self._planned_reconnect:
            self._planned_reconnect = True
            self._reconnect_deadline = time.monotonic() + 30.0
            self._reconnect_started_at = datetime.now(timezone.utc).isoformat()
            self._reconnect_previous_trade_at = self._last_trade_observed_at
            self._reconnect_previous_trade_clock = self._last_trade_observed_clock
            self._reconnect_first_trade_at = None
            self._reconnect_gap_seconds = None
            self._reconnect_outcome = "pending"
            self._reconnect_expiry_task = asyncio.create_task(self._expire_planned_reconnect(), name="realtime-reconnect-deadline")
        self._publish_connection_status()

    def _finish_planned_reconnect(self, outcome: str) -> None:
        if self._reconnect_started_at is None:
            return
        self._planned_reconnect = False
        if self._reconnect_outcome == "pending":
            self._reconnect_outcome = outcome
        task, self._reconnect_expiry_task = self._reconnect_expiry_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._publish_connection_status()
        if outcome == "failed":
            self._hub.publish({"type": "connection_failed", "message": "실시간 재연결을 완료하지 못했습니다.",
                               "connection_status": self.credential_connection_status()})

    async def _expire_planned_reconnect(self) -> None:
        try:
            await asyncio.sleep(max(0.0, self._reconnect_deadline - time.monotonic()))
            if not self._planned_reconnect:
                return
            codes, nxt_codes = self._hub.requested_codes()
            session = self._market_session()
            if session is None or (session == "NXT" and not nxt_codes) or (
                    not codes and not (session == "KRX" and self._market_events is not None)):
                self._credential_phase = "WAITING_MARKET" if session is None else "WAITING_SUBSCRIPTION"
                self._finish_planned_reconnect("waiting_market" if session is None else "waiting_subscription")
                return
            self._finish_planned_reconnect("deadline_exceeded")
            self._hub.publish({"type": "connection_failed", "message": "실시간 재연결 대기 시간이 지났습니다.",
                               "connection_status": self.credential_connection_status()})
        finally:
            if self._reconnect_expiry_task is asyncio.current_task():
                self._reconnect_expiry_task = None

    def _observe_reconnect_trade(self) -> None:
        observed_clock = time.monotonic()
        self._last_trade_observed_at = datetime.now(timezone.utc).isoformat()
        self._last_trade_observed_clock = observed_clock
        if (self._reconnect_started_at is not None and self._reconnect_first_trade_at is None
                and self._credential_phase == "READY" and not self._credential_paused):
            self._reconnect_first_trade_at = self._last_trade_observed_at
            if self._reconnect_previous_trade_clock is not None:
                self._reconnect_gap_seconds = max(0.0, observed_clock - self._reconnect_previous_trade_clock)
            self._publish_connection_status()
            logger.info("실전 재연결 체결 관측 간격: generation=%s gap_seconds=%s",
                        self._connection_generation, self._reconnect_gap_seconds)

    async def begin_credential_change(self) -> None:
        if self._credential_shutdown:
            raise RuntimeError("REALTIME_CLOSED")
        if self._credential_drain_task is None:
            self._credential_paused = True
            self._connection_generation += 1
            self._credential_phase = "DRAINING"
            self._hub.set_upstream_ready((), False)
            self._start_planned_reconnect()
            self._credential_drain_task = asyncio.create_task(
                self._drain_for_credentials(), name="realtime-credential-drain")
        await asyncio.shield(self._credential_drain_task)

    async def _drain_for_credentials(self) -> None:
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            finally:
                if self._task is task:
                    self._task = None
        if self._token_tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tuple(self._token_tasks)), return_exceptions=True)
        if self._boundary_task is not None:
            await asyncio.shield(self._boundary_task)
        self._hub.set_upstream_ready((), False)
        self._minute_bars.mark_capture_gap()
        self._continuous_from.clear()
        try:
            await self._flush_snapshots()
        except Exception:
            # Failed values remain in the existing retry buffers; preserve them
            # without turning a storage fault into an authentication fault.
            logger.exception("실전 인증 전환 중 저장 실패; 기존 대기 자료를 보존합니다")
        self._credential_phase = "PAUSED"
        self._publish_connection_status()

    async def end_credential_change(self) -> None:
        if self._credential_resume_task is None:
            self._credential_resume_task = asyncio.create_task(
                self._resume_after_credentials(), name="realtime-credential-resume")
            def completed(done):
                if self._credential_resume_task is done:
                    self._credential_resume_task = None
                if not done.cancelled():
                    done.exception()
            self._credential_resume_task.add_done_callback(completed)
        task = self._credential_resume_task
        try:
            await asyncio.shield(task)
        finally:
            if task.done() and self._credential_resume_task is task:
                self._credential_resume_task = None

    async def _resume_after_credentials(self) -> None:
        if self._credential_shutdown:
            raise RuntimeError("REALTIME_CLOSED")
        if self._credential_drain_task is None or not self._credential_drain_task.done():
            raise RuntimeError("REALTIME_DRAIN_NOT_COMPLETE")
        self._credential_drain_task.result()
        self._credential_paused = False
        self._credential_phase = "CONNECTING"
        self._publish_connection_status()
        try:
            await self.start()
        except Exception:
            self._credential_paused = True
            self._credential_phase = "FAILED"
            self._finish_planned_reconnect("failed")
            raise
        self._credential_drain_task = None

    async def start(self) -> None:
        if self._close_task is not None:
            if not self._close_task.done():
                raise RuntimeError("REALTIME_CLOSE_IN_PROGRESS")
            self._close_task.result()
            self._close_task = None
            self._credential_drain_task = None
            self._credential_resume_task = None
            self._credential_paused = False
        self._credential_shutdown = False
        if not self._credential_paused and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run(), name="kiwoom-central-realtime")
        if self._store is not None and self._snapshot_task is None:
            self._snapshot_task = asyncio.create_task(self._save_snapshots(), name="kiwoom-realtime-snapshots")

    async def close(self) -> None:
        self._credential_shutdown = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="realtime-close")
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        expiry_task = self._reconnect_expiry_task
        self._finish_planned_reconnect("stopped")
        if expiry_task is not None:
            await asyncio.gather(expiry_task, return_exceptions=True)
        if self._credential_resume_task is not None:
            await asyncio.gather(asyncio.shield(self._credential_resume_task), return_exceptions=True)
        if self._credential_drain_task is not None:
            await asyncio.shield(self._credential_drain_task)
        self._credential_paused = True
        self._connection_generation += 1
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
        if self._token_tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tuple(self._token_tasks)), return_exceptions=True)
        if self._boundary_task is not None:
            await asyncio.shield(self._boundary_task)
        self._hub.set_upstream_ready((), False)
        self._credential_phase = "STOPPED"

    async def _run(self) -> None:
        while not self._credential_paused:
            await self._notify_market_event_boundaries()
            codes, nxt_codes = self._hub.requested_codes()
            session = self._market_session()
            event_only = session == "KRX" and self._market_events is not None
            if (not codes and not event_only) or session is None or (session == "NXT" and not nxt_codes):
                await asyncio.sleep(1)
                continue
            try:
                await self._receive(session)
                if self._credential_paused:
                    return
                self._hub.set_upstream_ready((), False)
                self._deliver_account_event("disconnected", self._environment)
                self._credential_phase = "RECONNECTING" if self._market_session() == session else "WAITING_MARKET"
                if self._market_session() == session:
                    self._minute_bars.mark_capture_gap()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if self._credential_paused:
                    return
                self._credential_phase = "FAILED"
                self._hub.set_upstream_ready((), False)
                self._deliver_account_event("disconnected", self._environment)
                self._abnormal_disconnects += 1
                self._minute_bars.mark_capture_gap()
                logger.warning("키움 중앙 실시간 연결 실패: %s", error)
                self._hub.publish({"type": "connection_failed", "message": str(error),
                                   "connection_status": self.credential_connection_status()})
                self._publish_diagnostics(str(error))
                await asyncio.sleep(3)

    async def _receive(self, session: str) -> None:
        generation = self._connection_generation
        token_task = asyncio.create_task(asyncio.to_thread(self._token_provider), name="realtime-owned-token")
        self._token_tasks.add(token_task)
        token_task.add_done_callback(self._token_tasks.discard)
        token_task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        token = await asyncio.shield(token_task)
        if self._credential_paused or generation != self._connection_generation:
            return
        uri = f"{WS_BASE_URLS[self._environment]}/api/dostk/websocket"
        async with connect(uri, open_timeout=15, ping_interval=None) as websocket:
            await websocket.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if login.get("return_code") not in (None, 0, "0"):
                raise RuntimeError(f"WebSocket 로그인 실패: {login.get('return_msg', '')}")
            logger.info("키움 중앙 실시간 로그인 성공: %s", session)
            if self._credential_paused or generation != self._connection_generation:
                return
            self._credential_phase = "REGISTERING"
            subscribed: tuple[str, ...] = ()
            subscribed_nxt: tuple[str, ...] = ()
            subscribed_program: tuple[str, ...] = ()
            subscribed_priority: tuple[str, ...] = ()
            subscription_sent = False
            if self._connected_once:
                self._reconnects += 1
            self._connected_once = True
            self._publish_diagnostics("")
            market_events_connected = False
            registered_groups: dict[str, list[dict[str, object]]] = {}
            pending_registration_acks = 0
            pending_added: tuple[str, ...] = ()
            pending_trade_sources: set[tuple[str, str]] = set()
            policy_signature: tuple[str, ...] = ()
            connection_approved = False
            while self._market_session() is not None:
                await self._notify_market_event_boundaries()
                codes, nxt_codes = self._hub.requested_codes()
                target = realtime_subscription_target(
                    codes, set(nxt_codes), self._now_provider(), environment=self._environment,
                )
                requested_program = self._hub.requested_program_codes()
                priority_codes = self._hub.requested_priority_codes()
                event_only = self._market_events is not None and self._market_session() == "KRX"
                session = "KRX" if target.krx_codes or event_only else "NXT"
                if not target.active_codes and not event_only:
                    return
                if event_only and not market_events_connected:
                    await self._market_events.on_ws_connected(websocket)
                    market_events_connected = True
                elif market_events_connected:
                    poll_conditions = getattr(self._market_events, "poll_condition_updates", None)
                    if poll_conditions is not None:
                        await poll_conditions(websocket)
                if (
                    not subscription_sent
                    or target.active_codes != subscribed
                    or target.nxt_codes != subscribed_nxt
                    or requested_program != subscribed_program
                    or priority_codes != subscribed_priority
                    or target.signature != policy_signature
                ):
                    registered_groups = await self._send_subscription(
                        websocket, session, target.active_codes, target.nxt_codes,
                        requested_program, priority_codes,
                        previous_groups=registered_groups,
                    )
                    pending_registration_acks = len(registered_groups)
                    subscription_sent = True
                    effective = target.active_codes
                    previous_effective = subscribed_nxt if session == "NXT" else subscribed
                    pending_added = tuple(code for code in effective if code not in previous_effective)
                    pending_trade_sources = _registered_trade_sources(registered_groups)
                    subscribed, subscribed_nxt = target.active_codes, target.nxt_codes
                    subscribed_program = requested_program
                    subscribed_priority = priority_codes
                    policy_signature = target.signature
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=1)
                except TimeoutError:
                    continue
                if self._credential_paused or generation != self._connection_generation:
                    return
                message = json.loads(raw)
                if str(message.get("trnm", "")).upper() == "PING":
                    await websocket.send(json.dumps(message))
                    continue
                if str(message.get("trnm", "")).upper() == "REG":
                    if message.get("return_code") not in (None, 0, "0"):
                        raise RuntimeError(f"WebSocket 구독 실패: {message.get('return_msg', '')}")
                    pending_registration_acks = max(0, pending_registration_acks - 1)
                    if pending_registration_acks:
                        continue
                    logger.info("키움 중앙 실시간 구독 승인: %s · %s종목", session, len(subscribed))
                    self._credential_phase = "READY"
                    self._finish_planned_reconnect("registered")
                    self._hub.set_upstream_ready(subscribed, True)
                    self._deliver_account_event("connected", self._environment)
                    self._hub.publish({
                        "type": "connection_opened", "scope": "upstream",
                        "codes": list(subscribed),
                        "connection_status": self.credential_connection_status(),
                    })
                    subscribed_at = self._now_provider()
                    added_sources = pending_trade_sources - self._approved_trade_sources
                    removed_sources = self._approved_trade_sources - pending_trade_sources
                    reset_sources = added_sources if connection_approved else pending_trade_sources
                    self._minute_bars.reset_cumulative_sources(reset_sources)
                    self._second_trades.reset_cumulative_sources(reset_sources)
                    for subscription_key in removed_sources:
                        self._continuous_from.pop(subscription_key, None)
                    for subscription_key in reset_sources:
                        self._continuous_from[subscription_key] = subscribed_at
                    connection_approved = True
                    self._approved_trade_sources = set(pending_trade_sources)
                    pending_trade_sources = set()
                    if pending_added:
                        self._hub.publish({"type": "codes_added", "codes": list(pending_added)})
                    self._hub.publish({"type": "subscription_ready"})
                    pending_added = ()
                    continue
                if market_events_connected and self._market_events is not None:
                    await self._market_events.handle_ws_message(message, websocket)
                    if str(message.get("trnm", "")).upper() == "REAL":
                        if contains_krx_observation(message):
                            self._market_events.mark_krx_session_observed(self._now_provider().date().isoformat())
                self._publish_parsed(message)
            await self._notify_market_event_boundaries()

    def _publish_parsed(self, message: dict[str, Any]) -> None:
        if (str(message.get("trnm", "")).upper() == "REAL"
                and self._account_event_handler is not None and self._account_scope_resolver is not None):
            for row in message.get("data") or ():
                if not isinstance(row, dict) or row.get("type") not in {"00", "04"}:
                    continue
                values = row.get("values")
                raw = str(values.get("9201", "")).strip() if isinstance(values, dict) else ""
                scope = self._account_scope_resolver(raw) if raw else None
                if scope is not None:
                    self._deliver_account_event("order_changed" if row["type"] == "00" else "account_balance_changed", scope)
        for name, parser in (
            ("trade", parse_trade_ticks),
            ("order_execution", lambda value: parse_order_executions(
                value, self._account_scope_resolver,
            )),
            ("account_balance", lambda value: parse_account_balance_changes(
                value, self._account_scope_resolver,
            )),
            ("market_state", parse_market_index_ticks), ("program_trade", parse_program_trade_ticks),
            ("stock_reference", parse_stock_price_references),
        ):
            for value in parser(message):
                if (
                    name == "trade" and self._approved_trade_sources
                    and (str(value.code), str(value.market).upper())
                    not in self._approved_trade_sources
                ):
                    continue
                payload = asdict(value)
                if name in {"order_execution", "account_balance"}:
                    self._deliver_account_event(name, value)
                    self.publish_account_event(name, value)
                    continue
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
                    self._observe_reconnect_trade()
                    now = self._now_provider()
                    received_at = time.time()
                    self._minute_bars.add(
                        value, now, received_at,
                        capture_complete=self._minute_capture_complete(value, now),
                    )
                    self._second_trades.add(value, now, received_at)
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
                elif name == "stock_reference" and code:
                    observed_at = self._now_provider()
                    self._pending_stock_references[code] = {
                        "owner": code,
                        "key": "latest",
                        "document": {
                            **payload,
                            "observed_at": observed_at.isoformat(timespec="seconds"),
                            "source": "kiwoom-websocket-0g",
                        },
                    }
    def publish_account_event(self, event_type, value):
        """Publish parsed account events, including verified non-market profiles."""
        if event_type not in {"order_execution", "account_balance"}:
            return
        payload = asdict(value)
        code = str(payload.get("code", ""))
        self._hub.publish({"type": event_type, "payload": payload}, code)
        if event_type == "order_execution" and payload.get("side") == "매수" and code:
            observed_at = self._now_provider()
            day = observed_at.date().isoformat()
            self._pending_account_entry_symbols[(day, code)] = {
                "owner": day, "key": code, "document": {
                    "code": code, "environment": self._environment,
                    "last_executed_at": observed_at.isoformat(timespec="seconds"), "source": "kiwoom-realtime-00"}}

    def _deliver_account_event(self, event_type, value):
        if self._credential_paused or self._account_event_handler is None:
            return
        try:
            self._account_event_handler(event_type, value)
        except Exception:
            logger.warning("계좌 이벤트 전달 실패: %s", event_type)

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
        task = asyncio.create_task(self._flush_serialized(), name="realtime-owned-save")
        self._flush_tasks.add(task)
        task.add_done_callback(self._flush_tasks.discard)
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        await asyncio.shield(task)

    async def _flush_serialized(self) -> None:
        async with self._flush_lock:
            await self._flush_snapshot_cycle()

    async def _flush_snapshot_cycle(self) -> None:
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
        entry_symbols = self._pending_account_entry_symbols
        entry_values = list(entry_symbols.values())
        self._pending_account_entry_symbols = {}
        if entry_values:
            try:
                await asyncio.to_thread(
                    self._store.upsert_documents,
                    "account_entry_symbols_daily",
                    entry_values,
                )
            except Exception:
                for value in entry_values:
                    self._pending_account_entry_symbols[
                        (str(value["owner"]), str(value["key"]))
                    ] = value
                raise
        reference_values = list(self._pending_stock_references.values())
        self._pending_stock_references.clear()
        if reference_values:
            try:
                await asyncio.to_thread(
                    self._store.upsert_documents,
                    "stock_price_references",
                    reference_values,
                )
            except Exception:
                for value in reference_values:
                    self._pending_stock_references[str(value["owner"])] = value
                raise
        closures = list(self._pending_minute_finalizations.values())
        self._pending_minute_finalizations.clear()
        closures.extend(self._minute_bars.drain_closed(self._now_provider()))
        if closures:
            try:
                await asyncio.to_thread(self._store.finalize_minute_bars, closures)
            except Exception:
                for value in closures:
                    key = tuple(
                        str(value[name])
                        for name in ("trading_date", "minute", "code", "market")
                    )
                    self._pending_minute_finalizations[key] = value
                raise
        drained_second_bars = self._second_trades.drain_dirty()
        for value in drained_second_bars:
            self._keep_second_retry(value)
        second_bars = list(self._pending_second_retries.values())
        self._pending_second_retries.clear()
        if second_bars:
            try:
                await asyncio.to_thread(self._store.save_second_trade_bars, second_bars)
            except Exception:
                for value in second_bars:
                    self._keep_second_retry(value)
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
        """저장하지 못한 delta를 operation ID 그대로 보존한다."""
        operation_id = str(value.get("operation_id") or "").strip()
        if not operation_id:
            raise ValueError("minute bar operation_id is required")
        previous = self._pending_minute_retries.get(operation_id)
        if previous is not None and previous != value:
            raise ValueError("minute bar operation_id payload changed")
        self._pending_minute_retries[operation_id] = dict(value)

    def _keep_second_retry(self, value: dict[str, Any]) -> None:
        """초 봉의 최신 절대 상태만 보관해 재시도 때 이중 합산을 막는다."""
        key = tuple(
            str(value[name]) for name in ("trading_date", "trade_second", "code", "market")
        )
        previous = self._pending_second_retries.get(key)
        if previous is None or (
            float(value["available_at"]), int(value["trade_count"])
        ) >= (
            float(previous["available_at"]), int(previous["trade_count"])
        ):
            self._pending_second_retries[key] = dict(value)

    def _minute_capture_complete(self, tick: Any, now: datetime) -> bool:
        market = str(tick.market or "KRX").upper()
        continuous_from = self._continuous_from.get((str(tick.code), market))
        if continuous_from is None:
            # 직접 fixture 주입처럼 구독 시작을 알 수 없는 기존 호출은 보수적으로
            # 처리하되, 실제 수집은 REG 승인 때 반드시 시각을 기록한다.
            return False
        raw = str(tick.trade_time or "")
        if len(raw) < 4 or not raw[:4].isdigit():
            return False
        bar_start = now.replace(
            hour=int(raw[:2]), minute=int(raw[2:4]), second=0, microsecond=0,
        )
        try:
            return continuous_from <= bar_start
        except TypeError:
            return False

    async def _send_subscription(
        self, websocket: Any, session: str, codes: tuple[str, ...], nxt_codes: tuple[str, ...],
        program_codes: tuple[str, ...] | None = None,
        priority_codes: tuple[str, ...] | None = None,
        *, previous_groups: dict[str, list[dict[str, object]]] | None = None,
    ) -> dict[str, list[dict[str, object]]]:
        active = tuple(dict.fromkeys(codes))
        nxt = set(nxt_codes)
        requested_program = tuple(
            code for code in dict.fromkeys(program_codes if program_codes is not None else active)
            if code in active
        )
        if len(active) > REALTIME_ITEMS_PER_TYPE:
            raise RuntimeError(
                f"중앙 실시간 필수 0B 대상이 타입별 한도"
                f"({REALTIME_ITEMS_PER_TYPE}종목)를 초과했습니다: {len(active)}종목"
            )
        # 0B와 0w는 서로 다른 실시간 type이다. 0w 등록 때문에 0B의
        # KRX/NXT 상세 자리를 줄이지 않고 각 type 안에서만 200개를 계산한다.
        selected_program = requested_program[:REALTIME_ITEMS_PER_TYPE]
        venue_detail_budget = REALTIME_ITEMS_PER_TYPE - len(active)
        # 상세 venue는 TOP20 -> 실제 보유/매수 -> 나머지 앱 요청 순으로 채운다.
        # priority_codes가 있어도 일반 요청 종목을 후보에서 빠뜨리지 않는다.
        detail_order = tuple(dict.fromkeys((*(priority_codes or ()), *requested_program)))
        detailed = set(
            code for code in detail_order
            if code in active and code in nxt
        )
        if len(detailed) > venue_detail_budget:
            detailed = set(tuple(
                code for code in detail_order if code in detailed
            )[:venue_detail_budget])
        if session == "NXT":
            items = tuple(f"{code}_NX" for code in active if code in nxt)
        else:
            item_values: list[str] = []
            for code in active:
                if code in detailed:
                    item_values.extend((code, f"{code}_NX"))
                elif self._environment == "real" and code in nxt:
                    item_values.append(f"{code}_AL")
                else:
                    item_values.append(code)
            items = tuple(item_values)
        program_items = tuple(
            f"{code}_AL" for code in selected_program
        ) if self._environment == "real" else selected_program
        groups: dict[str, list[dict[str, object]]] = {}
        for index, chunk in enumerate(_chunks(items)):
            groups[str(1000 + index)] = [{"item": list(chunk), "type": ["0B"]}]
        for index, chunk in enumerate(_chunks(program_items)):
            groups[str(2000 + index)] = [{"item": list(chunk), "type": ["0w"]}]
        for index, chunk in enumerate(_chunks(active)):
            groups[str(4000 + index)] = [{"item": list(chunk), "type": ["0g"]}]
        groups["3000"] = [
            {"item": [""], "type": ["00", "04"]},
            {"item": ["001", "101"], "type": ["0J", "0U"]},
            *([{"item": [], "type": ["1h"]}]
              if session == "KRX" and self._market_events is not None else []),
        ]
        previous_groups = previous_groups or {}
        for group_no in sorted(set(previous_groups) - set(groups)):
            await self._send_registration(websocket, json.dumps({
                "trnm": "REMOVE", "grp_no": group_no,
                "data": previous_groups[group_no],
            }))
        for group_no, data in groups.items():
            await self._send_registration(websocket, json.dumps({
                "trnm": "REG", "grp_no": group_no, "refresh": "0", "data": data,
            }))
        return groups

    async def _send_registration(self, websocket: Any, raw: str) -> None:
        wait_seconds = (
            self._last_registration_sent_at + REALTIME_REG_INTERVAL_SECONDS
            - time.monotonic()
        )
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        await websocket.send(raw)
        self._last_registration_sent_at = time.monotonic()

    def _market_session(self) -> str | None:
        now = self._now_provider()
        target = realtime_subscription_target(
            ("_probe",), {"_probe"}, now, environment=self._environment,
        )
        if target.krx_codes:
            return "KRX"
        if target.nxt_codes:
            return "NXT"
        return None

    async def _notify_market_event_boundaries(self) -> None:
        if self._market_events is None:
            return
        if self._boundary_task is None:
            self._boundary_task = asyncio.create_task(
                self._notify_market_event_boundary_cycle(), name="realtime-owned-market-close")
            def completed(done):
                if self._boundary_task is done:
                    self._boundary_task = None
                if not done.cancelled():
                    done.exception()
            self._boundary_task.add_done_callback(completed)
        await asyncio.shield(self._boundary_task)

    async def _notify_market_event_boundary_cycle(self) -> None:
        if self._market_events is None:
            return
        now = self._now_provider()
        session_id = now.date().isoformat()
        current = now.time().replace(tzinfo=None)
        if current >= clock_time(15, 30) and session_id not in self._regular_close_notified:
            close_regular = getattr(self._market_events, "close_krx_regular_session", None)
            if close_regular is not None:
                await close_regular(session_id)
            self._regular_close_notified.add(session_id)
        if current >= clock_time(20) and session_id not in self._full_day_close_notified:
            close_day = getattr(self._market_events, "close_observation_day", None)
            if close_day is not None:
                await close_day(session_id)
            self._full_day_close_notified.add(session_id)

    def _publish_diagnostics(self, reason: str) -> None:
        self._hub.publish({"type": "diagnostics", "payload": {
            "abnormal_disconnects": self._abnormal_disconnects,
            "reconnects": self._reconnects,
            "last_disconnect_reason": reason,
            "updated_at": self._now_provider().isoformat(timespec="seconds"),
        }})
