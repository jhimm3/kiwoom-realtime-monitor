from __future__ import annotations

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_registration import (
    RegistrationProgress, all_atoms, encode_groups, filter_message, group_atoms as group_atoms_for_live, plan_registration, ready_codes, trade_sources,
)

import asyncio
from .diagnostic_replay_runtime import owned_create_task, owned_to_thread
import json
import logging
import time
from contextlib import contextmanager
from collections import deque
from itertools import chain
from uuid import uuid4
from dataclasses import asdict
from datetime import datetime, time as clock_time, timedelta, timezone
from typing import Any, Awaitable, Callable, Protocol

from websockets.asyncio.client import connect

from kiwoom_monitor.application.market_session_schedule import realtime_subscription_target

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    parse_account_balance_changes, parse_market_index_ticks, parse_market_operation_ticks,
    parse_order_executions,
    parse_program_trade_ticks, parse_stock_price_references, parse_trade_ticks,
)
from kiwoom_monitor.domain.market_data_contract import MarketDataObservation
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    DataValueKind,
    ObservationOrigin,
)
from kiwoom_monitor.domain.order_contract import AccountScope
from .realtime_hub import (
    RealtimeHub, CapturedMessageReceipt, CapturedParserReceipt, CapturedHubEvent,
)
from .diagnostic_replay_contract import (
    COLLECTOR_EVENT_FIELDS, COLLECTOR_INPUT_VERSION, captured_workload, validate_collector_message,
)
from .diagnostic_top20_lifecycle_input import captured_subscription
from .minute_bars import MinuteBarAccumulator
from .market_observations import (
    KST,
    as_kst,
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
SUBSCRIPTION_ACK_TIMEOUT_SECONDS = 15.0
LATEST_CHECKPOINT_SECONDS = 300
LATEST_CHECKPOINT_OFFSET_SECONDS = 10


class RealtimeCollectorStore(Protocol):
    """실시간 collector의 snapshot·bar 저장 및 복구 조회 계약."""

    def save_realtime_snapshots(self, values: list[dict[str, Any]]) -> None: ...

    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None: ...

    def save_second_trade_bars(self, values: list[dict[str, Any]]) -> None: ...

    def finalize_minute_bars(self, values: list[dict[str, Any]]) -> None: ...

    def load_minute_bars(
        self, code: str, trading_date: str, market: str = "", *,
        realtime_deltas: list[dict[str, Any]] | None = None,
        start_minute: str | None = None, end_minute: str | None = None,
    ) -> list[dict[str, Any]]: ...
    def load_recent_minute_bars(
        self, code: str, start_date: str, end_date: str, market: str = "", trading_days: int = 2, *,
        realtime_deltas: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]: ...

    def save_dataset_snapshots(
        self,
        values: list[tuple[
            str, str, str, dict[str, Any], MarketDataObservation[object] | None,
        ]],
    ) -> None: ...

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...


def _next_latest_checkpoint(now: float) -> float:
    """Next five-minute wall-clock boundary plus ten seconds (also in KST)."""
    boundary = int(now // LATEST_CHECKPOINT_SECONDS) * LATEST_CHECKPOINT_SECONDS
    due = boundary + LATEST_CHECKPOINT_OFFSET_SECONDS
    return float(due if due > now else due + LATEST_CHECKPOINT_SECONDS)


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
        now_provider: Callable[[], datetime], store: RealtimeCollectorStore | None = None,
        market_events: Any | None = None,
        account_scope_resolver: Callable[[str], AccountScope | None] | None = None,
        account_event_handler: Callable[[str, object], None] | None = None,
        *, snapshot_sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._environment = environment
        self._hub = hub
        self._realtime_tape_version = hub._realtime_tape_version = "top20-realtime-tape/v2"
        self._subscription_filter_active = False
        self._now_provider = now_provider
        self._store = store
        self._market_events = market_events
        self._account_scope_resolver = account_scope_resolver
        self._account_event_handler = account_event_handler
        self._automation_price_listener = None
        self._breakout_trade_listener = None
        self._snapshot_sleep = snapshot_sleep or asyncio.sleep
        self._input_replay_only = False
        self._task: asyncio.Task[None] | None = None
        self._snapshot_task: asyncio.Task[None] | None = None
        self._pending_snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        self._latest_snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending_account_entry_symbols: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending_stock_references: dict[str, dict[str, Any]] = {}
        self._pending_market_history: dict[
            str,
            tuple[str, str, dict[str, Any], MarketDataObservation[dict[str, Any]]],
        ] = {}
        self._pending_minute_retries: dict[str, dict[str, Any]] = {}
        self._inflight_minute_bars: list[dict[str, Any]] = []
        self._pending_minute_finalizations: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._minute_bars = MinuteBarAccumulator()
        # Individual 0B receipts, owned by this collector; never persisted as bars.
        self._ram_trades: deque[dict[str, Any]] = deque()
        self._ram_trade_baselines: dict[tuple[str, str], tuple] = {}
        self._ram_trade_sequence = 0
        self._ram_trade_retention_seconds = 0
        self._ram_source_id = uuid4().hex
        self._ram_observation_generation = 0
        self._ram_observation_gap_at = None
        self._ram_retention_since = None
        self._ram_observation_owner = None
        self._ram_stock_references: dict[tuple[str, str], dict[str, Any]] = {}
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
        self._save_success_at: dict[str, str] = {}
        self._save_failures: dict[str, str] = {}
        self._active_save: dict[str, object] | None = None
        self._reconnect_previous_trade_at: str | None = None
        self._reconnect_previous_trade_clock: float | None = None
        self._reconnect_first_trade_at: str | None = None
        self._reconnect_gap_seconds: float | None = None

    @staticmethod
    def _save_record_summary(records) -> dict[str, object]:
        """Retained record times, not first enqueue times or unique event counts."""
        oldest = None
        known = 0
        count = 0
        for record in records:
            count += 1
            document = record.get("document", {})
            raw = next((record.get(key) for key in ("received_at", "updated_at", "available_at")
                        if record.get(key) is not None), None)
            if raw is None:
                raw = document.get("observed_at") or document.get("last_executed_at")
            try:
                at = (datetime.fromisoformat(raw).timestamp() if isinstance(raw, str)
                      else raw.timestamp() if isinstance(raw, datetime) else float(raw))
                # Conversion also rejects out-of-range/non-finite timestamps.
                datetime.fromtimestamp(at, timezone.utc)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            known += 1
            if oldest is None or at < oldest:
                oldest = at
        return {"records": count,
                "oldest_record_at": datetime.fromtimestamp(oldest, KST).isoformat() if oldest is not None else None,
                "timed_records": known}

    def operational_status(self) -> dict[str, object]:
        """Read in-memory owner state without polling the DB or draining buffers."""
        pending = {
            "snapshots": self._save_record_summary(self._pending_snapshots.values()),
            "minute_bars": self._save_record_summary(chain(
                ({"updated_at": self._minute_bars._bars[key].updated_at} for key in self._minute_bars._dirty),
                self._pending_minute_retries.values(),
            )),
            "account_entry_symbols": self._save_record_summary(self._pending_account_entry_symbols.values()),
            "stock_references": self._save_record_summary(self._pending_stock_references.values()),
            "minute_finalizations": self._save_record_summary(self._pending_minute_finalizations.values()),
            # Empty compatibility bucket in the existing diagnostic HTTP v1.
            "second_bars": self._save_record_summary(()),
            "market_history": self._save_record_summary(
                {"available_at": value[3].metadata.available_at}
                for value in self._pending_market_history.values()),
        }
        inflight = dict(self._active_save) if self._active_save is not None else None
        summaries = [*pending.values(), *([inflight] if inflight else [])]
        times = [row["oldest_record_at"] for row in summaries if row["oldest_record_at"] is not None]
        return {
            "scope": "realtime_collector", "measurement": "process_local",
            "receive": {
                "scope": "accepted_trade_0B", "last_received_at": self._last_trade_observed_at,
                "age_seconds": (max(0.0, time.monotonic() - self._last_trade_observed_clock)
                                if self._last_trade_observed_clock is not None else None),
            },
            "persistence": {
                "state": "not_configured" if self._store is None else
                         "degraded" if self._save_failures else
                         "saving" if inflight else
                         "observed" if self._save_success_at else "unknown",
                "last_success_at": max(self._save_success_at.values(), default=None),
                "last_success_by_kind": dict(self._save_success_at),
                "unresolved_failures": dict(self._save_failures),
                "pending_records": sum(int(row["records"]) for row in pending.values()),
                "inflight_records": int(inflight["records"]) if inflight else 0,
                "oldest_record_at": min(times, default=None),
                "timed_records": sum(int(row["timed_records"]) for row in summaries),
                "record_time_basis": "retained_record_observation_time_not_queue_entry",
                "pending_by_kind": pending, "inflight": inflight,
                "waiting_flushes": max(0, len(self._flush_tasks) - int(self._flush_lock.locked())),
            },
        }

    @contextmanager
    def _observe_save(self, kind: str, records):
        # The existing serialized flush and owned native await retain ownership.
        self._active_save = {
            "kind": kind, **self._save_record_summary(records),
            "started_at": as_kst(self._now_provider()).isoformat(),
        }
        try:
            yield
        except BaseException:
            self._save_failures[kind] = as_kst(self._now_provider()).isoformat()
            raise
        else:
            self._save_success_at[kind] = as_kst(self._now_provider()).isoformat()
            self._save_failures.pop(kind, None)
        finally:
            self._active_save = None

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
            self._reconnect_started_at = as_kst(self._now_provider()).isoformat()
            self._reconnect_previous_trade_at = self._last_trade_observed_at
            self._reconnect_previous_trade_clock = self._last_trade_observed_clock
            self._reconnect_first_trade_at = None
            self._reconnect_gap_seconds = None
            self._reconnect_outcome = "pending"
            self._reconnect_expiry_task = owned_create_task(self._expire_planned_reconnect(), name="realtime-reconnect-deadline")
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
        self._last_trade_observed_at = as_kst(self._now_provider()).isoformat()
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
            self._credential_drain_task = owned_create_task(
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
        self._mark_capture_gap(clear_continuous=True)
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
            self._credential_resume_task = owned_create_task(
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
        if self._input_replay_only:
            raise RuntimeError("INPUT_REPLAY_CANNOT_OPEN_NETWORK")
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
            self._task = owned_create_task(self._run(), name="kiwoom-central-realtime")
        if self._store is not None and self._snapshot_task is None:
            self._snapshot_task = owned_create_task(self._save_snapshots(), name="kiwoom-realtime-snapshots")

    async def start_input_replay(self) -> None:
        """Start the real persistence loop on a fresh, isolated market collector."""
        if (self._task is not None or self._snapshot_task is not None or self._close_task is not None
                or self._input_replay_only or self._store is None or self._market_events is not None
                or self._account_scope_resolver is not None or self._account_event_handler is not None):
            raise RuntimeError("INPUT_REPLAY_REQUIRES_FRESH_MARKET_COLLECTOR")
        self._input_replay_only = True
        self._snapshot_task = owned_create_task(self._save_snapshots(), name="kiwoom-replay-snapshots")

    def accept_replay_message(self, message: dict[str, Any]) -> None:
        """Replay only allowlisted market input; account events cannot enter here."""
        if not self._input_replay_only or self._credential_shutdown:
            raise RuntimeError("INPUT_REPLAY_NOT_RUNNING")
        validate_collector_message(message)
        self._publish_parsed(message)

    def restore_replay_source_state(
        self, sources: set[tuple[str, str]], continuous_from: dict[tuple[str, str], datetime],
    ) -> None:
        """Restore source boundaries only; this is not a warm accumulator restore."""
        if not self._input_replay_only or self._credential_shutdown:
            raise RuntimeError("INPUT_REPLAY_NOT_RUNNING")
        if self._approved_trade_sources or self._pending_snapshots or self._minute_bars._dirty:
            raise RuntimeError("INPUT_REPLAY_SOURCE_STATE_REQUIRES_FRESH_COLLECTOR")
        if (not set(continuous_from).issubset(sources)
                or any(not isinstance(at, datetime) or at.tzinfo is None
                       for at in continuous_from.values())):
            raise ValueError("INPUT_REPLAY_SOURCE_STATE_INVALID")
        self._approved_trade_sources = set(sources)
        self._continuous_from = dict(continuous_from)

    def accept_replay_sources(self, sources: set[tuple[str, str]], *, reset_all: bool = False,
                              subscribed_at: datetime | None = None) -> None:
        if not self._input_replay_only or self._credential_shutdown:
            raise RuntimeError("INPUT_REPLAY_NOT_RUNNING")
        self._apply_trade_source_approval(sources, subscribed_at or self._now_provider(), reset_all=reset_all)

    def accept_replay_gap(self, *, clear_continuous: bool = True) -> None:
        if not self._input_replay_only or self._credential_shutdown:
            raise RuntimeError("INPUT_REPLAY_NOT_RUNNING")
        self._mark_capture_gap()
        if clear_continuous:
            self._continuous_from.clear()

    def input_replay_status(self) -> dict[str, Any]:
        if not self._input_replay_only:
            raise RuntimeError("INPUT_REPLAY_NOT_RUNNING")
        return {"pending_records": len(self._pending_snapshots) + len(self._minute_bars._dirty)
                + len(self._pending_minute_retries)
                + len(self._inflight_minute_bars) + len(self._pending_minute_finalizations)
                + len(self._pending_market_history)
                + len(self._pending_stock_references) + len(self._pending_account_entry_symbols),
                "stopped": self._credential_phase == "STOPPED"}

    def _apply_trade_source_approval(
        self, sources: set[tuple[str, str]], subscribed_at: datetime, *, reset_all: bool,
    ) -> None:
        self._record_upstream("source_approval", {
            "sources": tuple(sorted(sources)), "subscribed_at": subscribed_at, "reset_all": reset_all,
        })
        added_sources = sources - self._approved_trade_sources
        removed_sources = self._approved_trade_sources - sources
        reset_sources = sources if reset_all else added_sources
        for key in removed_sources | reset_sources:
            self._ram_stock_references.pop(key, None)
        if reset_all or added_sources or removed_sources:
            self._ram_observation_generation += 1
        self._minute_bars.reset_cumulative_sources(reset_sources)
        for key in removed_sources | reset_sources:
            self._ram_trade_baselines.pop(key, None)
        self._ram_trades = deque(row for row in self._ram_trades
                                if (row['code'], row['market']) not in removed_sources | reset_sources)
        for key in removed_sources:
            self._continuous_from.pop(key, None)
        for key in reset_sources:
            self._continuous_from[key] = subscribed_at
        self._approved_trade_sources = set(sources)

    async def close(self) -> None:
        self._credential_shutdown = True
        if self._close_task is None:
            self._close_task = owned_create_task(self._close(), name="realtime-close", shutdown=True)
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
                    self._mark_capture_gap()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if self._credential_paused:
                    return
                self._credential_phase = "FAILED"
                self._hub.set_upstream_ready((), False)
                self._deliver_account_event("disconnected", self._environment)
                self._abnormal_disconnects += 1
                self._mark_capture_gap()
                logger.warning("키움 중앙 실시간 연결 실패: %s", error)
                self._hub.publish({"type": "connection_failed", "message": str(error),
                                   "connection_status": self.credential_connection_status()})
                self._publish_diagnostics(str(error))
                await asyncio.sleep(3)

    async def _receive(self, session: str) -> None:
        generation = self._connection_generation
        token_task = owned_create_task(owned_to_thread(self._token_provider), name="realtime-owned-token")
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
            from . import diagnostic_top20_subscription_v2 as recording
            groups, approved, subscribed = {}, set(), ()
            self._subscription_filter_active = True
            self._apply_trade_source_approval(set(), self._now_provider(), reset_all=False)
            progress = None
            batch = command_receipt = approval = None
            batch_request = None
            revision = 0
            batch_closed = True
            connection_approved = False
            published_codes = ()
            market_events_connected = False
            if self._connected_once:
                self._reconnects += 1
            self._connected_once = True
            self._publish_diagnostics("")
            recv_task = owned_create_task(websocket.recv(), name="realtime-owned-recv")
            send_task = None
            last_sent = float('-inf')
            deadline = 0.0
            try:
                while self._market_session() is not None:
                    if self._credential_paused or generation != self._connection_generation:
                        return
                    await self._notify_market_event_boundaries()
                    codes, nxt_codes = self._hub.requested_codes()
                    target = realtime_subscription_target(
                        codes, set(nxt_codes), self._now_provider(), environment=self._environment)
                    program = self._hub.requested_program_codes()
                    priority = self._hub.requested_priority_codes()
                    event_only = self._market_events is not None and self._market_session() == "KRX"
                    session = "KRX" if target.krx_codes or event_only else "NXT"
                    if not target.active_codes and not event_only:
                        return
                    if event_only and not market_events_connected:
                        await self._market_events.on_ws_connected(websocket)
                        market_events_connected = True
                    elif market_events_connected:
                        poll = getattr(self._market_events, "poll_condition_updates", None)
                        if poll is not None:
                            await poll(websocket)
                    desired = self._subscription_groups(session, target.active_codes, target.nxt_codes, program, priority)
                    wanted = all_atoms(desired)
                    if progress is None:
                        plan = plan_registration(desired, groups)
                        if plan.commands:
                            revision += 1
                            batch_request = {"session":session, "codes":list(target.active_codes),
                                "nxt_codes":list(target.nxt_codes), "program_codes":list(program),
                                "priority_codes":list(priority)}
                            batch = recording.begin_batch(self, batch_request, plan, revision)
                            batch_closed = False
                            progress = RegistrationProgress(plan)
                            approved.intersection_update(plan.retained)
                            retained_groups = encode_groups({group: atoms & approved
                                for group, atoms in group_atoms_for_live(groups).items()})
                            self._apply_trade_source_approval(trade_sources(retained_groups), self._now_provider(), reset_all=False)
                            if connection_approved:
                                covered = ready_codes(desired, approved, target.active_codes)
                                projected = recording.projection(self, batch, retained_groups, covered,
                                    desired=desired,previous=approval)
                                self._hub.set_upstream_ready(covered, True, approval=projected)
                                published_codes = covered
                    if approved - wanted:
                        approved.intersection_update(wanted)
                        kept = encode_groups({group: atoms & approved
                            for group, atoms in group_atoms_for_live(groups).items()})
                        self._apply_trade_source_approval(trade_sources(kept),self._now_provider(),reset_all=False)
                    allowed = approved & wanted
                    if connection_approved and progress is not None:
                        covered = ready_codes(desired,allowed,target.active_codes)
                        if covered != published_codes:
                            retained_groups = encode_groups({group:atoms & allowed
                                for group,atoms in group_atoms_for_live(groups).items()})
                            projected = recording.projection(self,batch,retained_groups,covered,
                                desired=desired,previous=approval)
                            self._hub.set_upstream_ready(covered,True,approval=projected)
                            published_codes = covered
                    # Keep the market/account parsers on their native path, but gate unapproved market rows.
                    now = time.monotonic()
                    if progress is not None and progress.started and now >= deadline:
                        raise RuntimeError("WebSocket 구독 승인 시간 초과")
                    if progress is not None and not progress.started and send_task is None:
                        if now >= last_sent + REALTIME_REG_INTERVAL_SECONDS:
                            command = progress.start()
                            command_receipt = recording.command_event(
                                self, 'command_start', batch, command, progress.index+1)
                            deadline = now + SUBSCRIPTION_ACK_TIMEOUT_SECONDS
                            send_task = owned_create_task(websocket.send(json.dumps(command.packet())),
                                                          name="realtime-subscription-send")
                    tasks = {recv_task}
                    if send_task is not None:
                        tasks.add(send_task)
                    wait = .1
                    if progress is not None:
                        due = deadline if progress.started else last_sent + REALTIME_REG_INTERVAL_SECONDS
                        wait = min(wait, max(0.0, due-time.monotonic()))
                    done, _ = await asyncio.wait(tasks, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                    committed = False
                    if send_task is not None and send_task in done:
                        send_task.result()
                        send_task = None
                        last_sent = time.monotonic()
                        recording.command_event(self, 'command_sent', batch, progress.command,
                                                progress.index+1, parent=command_receipt,
                                                sent_mono_ns=int(last_sent*1e9))
                        committed = progress.mark_sent()
                    if recv_task in done:
                        message = json.loads(recv_task.result())
                        recv_task = owned_create_task(websocket.recv(), name="realtime-owned-recv")
                        kind = str(message.get("trnm", "")).upper()
                        if kind == "PING":
                            await websocket.send(json.dumps(message))
                        elif kind in {"REG", "REMOVE"}:
                            if progress is None:
                                raise RuntimeError('realtime_ack_without_command')
                            recording.command_event(self, 'command_ack', batch, progress.command,
                                                    progress.index+1, parent=command_receipt, message=message)
                            committed = progress.accept_ack(message) or committed
                        else:
                            admitted = filter_message(message, allowed)
                            if market_events_connected:
                                await self._market_events.handle_ws_message(admitted, websocket)
                                if kind == 'REAL' and contains_krx_observation(admitted):
                                    self._market_events.mark_krx_session_observed(self._now_provider().date().isoformat())
                            self._publish_parsed(admitted)
                    if committed and progress.done:
                        groups = progress.plan.groups
                        latest_codes, latest_nxt = self._hub.requested_codes()
                        latest_target = realtime_subscription_target(latest_codes,set(latest_nxt),
                            self._now_provider(),environment=self._environment)
                        latest_session = 'KRX' if latest_target.krx_codes or event_only else 'NXT'
                        latest_groups = self._subscription_groups(latest_session,latest_target.active_codes,
                            latest_target.nxt_codes,self._hub.requested_program_codes(),self._hub.requested_priority_codes())
                        superseded = all_atoms(groups) != all_atoms(latest_groups)
                        completed = recording.end_batch(self, batch, groups,
                            outcome='superseded' if superseded else 'returned')
                        batch_closed = True
                        progress = None
                        if superseded:
                            continue
                        approved = all_atoms(groups)
                        initial = not connection_approved
                        approval = completed
                        self._approve_incremental_subscription(groups, target.active_codes, subscribed,
                            initial=initial, approval=approval)
                        connection_approved = True
                        subscribed = target.active_codes
                        published_codes = subscribed
                await self._notify_market_event_boundaries()
            except BaseException as error:
                if progress is not None:
                    recording.end_batch(self, batch, encode_groups(progress.groups),
                                        outcome='failed', error_type=type(error).__name__)
                    batch_closed = True
                raise
            finally:
                if progress is not None and not batch_closed:
                    recording.end_batch(self,batch,encode_groups(progress.groups),
                                        outcome='failed',error_type='SubscriptionInterrupted')
                tasks = [task for task in (recv_task, send_task) if task is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    def _approve_incremental_subscription(self, groups, codes, previous_codes, *, initial, approval=None):
        sources = trade_sources(groups)
        resets = sources - self._approved_trade_sources
        self._apply_trade_source_approval(sources, self._now_provider(), reset_all=initial)
        self._credential_phase = 'READY'
        self._finish_planned_reconnect('registered')
        self._hub.set_upstream_ready(codes, True, approval=approval)
        if initial:
            self._deliver_account_event('connected', self._environment)
            self._hub.publish({'type':'connection_opened', 'scope':'upstream', 'codes':list(codes),
                               'connection_status':self.credential_connection_status()})
        added = tuple(code for code in codes if code not in previous_codes)
        if added or resets:
            self._hub.publish({'type':'codes_added', 'codes':list(added),
                'source_resets':[{'code':code, 'market':market} for code,market in sorted(resets)]})
        self._hub.publish({'type':'subscription_ready', 'scope':'upstream', 'codes':list(codes),
                           'connection_status':self.credential_connection_status()})

    def _accept_registration_ack(
        self, message, pending, *, session, subscribed, sources, added, reset_all, approval=None,
    ) -> int:
        """Shared live/offline approval policy; only the final successful ACK is READY."""
        if message.get("return_code") not in (None, 0, "0"):
            raise RuntimeError(f"WebSocket 구독 실패: {message.get('return_msg', '')}")
        remaining = max(0, pending - 1)
        if remaining:
            return remaining
        logger.info("키움 중앙 실시간 구독 승인: %s · %s종목", session, len(subscribed))
        self._credential_phase = "READY"
        self._finish_planned_reconnect("registered")
        self._hub.set_upstream_ready(subscribed, True, approval=approval)
        self._deliver_account_event("connected", self._environment)
        self._hub.publish({
            "type": "connection_opened", "scope": "upstream", "codes": list(subscribed),
            "connection_status": self.credential_connection_status(),
        })
        self._apply_trade_source_approval(sources, self._now_provider(), reset_all=reset_all)
        if added:
            self._hub.publish({"type": "codes_added", "codes": list(added)})
        self._hub.publish({"type": "subscription_ready"})
        return remaining

    def _mark_capture_gap(self, *, clear_continuous: bool = False) -> None:
        from .diagnostic_top20_lifecycle_input import record_gap
        record_gap(self, clear_continuous)
        self._record_upstream("capture_gap", {"clear_continuous": clear_continuous})
        self._minute_bars.mark_capture_gap()
        self._ram_observation_generation += 1
        self._ram_observation_gap_at = self._now_provider()
        self._ram_stock_references.clear()
        self._ram_trades.clear()
        self._ram_trade_baselines.clear()

    def set_breakout_trade_listener(self, listener) -> None:
        """Attach the existing candidate owner; callbacks must not perform I/O."""
        self._breakout_trade_listener = listener

    def _observe_ram_trade(self, tick, now: datetime, received_at: float) -> None:
        """Retain individual approved 0B receipts, with original cumulative facts.

        Reuse the former received-trade price/volume combination without
        grouping into second bars. FID14's million-KRW delta stays separate.
        """
        key = (tick.code, str(tick.market or 'KRX').upper())
        if not self._ram_trade_retention_seconds or key not in self._approved_trade_sources:
            return
        if self._ram_trades and received_at < self._ram_trades[-1]['available_at']:
            self._ram_trades.clear()
            self._ram_trade_baselines.clear()
            self._ram_observation_gap_at = now
        try:
            observed = datetime.combine(now.astimezone(KST).date(),
                datetime.strptime(str(tick.trade_time), '%H%M%S').time(), tzinfo=KST)
            price = abs(int(tick.current_price))
            if now.tzinfo is None or observed > now or price <= 0:
                raise ValueError('invalid trade time or price')
        except (TypeError, ValueError):
            self._ram_observation_gap_at = now
            self._ram_trade_baselines.pop(key, None)
            return
        signature = (observed.isoformat(), price, tick.trade_volume,
                     tick.cumulative_volume, tick.cumulative_trade_value)
        previous = self._ram_trade_baselines.get(key)
        if previous is not None and previous[0] == observed.date() and previous[3] == signature:
            if tick.cumulative_volume is not None or tick.cumulative_trade_value is not None:
                return
        if previous is not None and (previous[0] != observed.date() or observed < previous[4]):
            self._ram_observation_gap_at = now
            previous = None
        turnover = None
        if (previous is not None and tick.cumulative_trade_value is not None and previous[2] is not None
                and tick.cumulative_trade_value >= previous[2]):
            turnover = (tick.cumulative_trade_value - previous[2]) * 1_000_000
        volume = abs(int(tick.trade_volume)) if tick.trade_volume is not None else None
        if volume is None and previous is not None and tick.cumulative_volume is not None and previous[1] is not None:
            if tick.cumulative_volume >= previous[1]:
                volume = tick.cumulative_volume - previous[1]
        if (previous is not None and tick.cumulative_volume is not None and previous[1] is not None
                and tick.cumulative_volume < previous[1]):
            self._ram_observation_gap_at = now
        self._ram_trade_baselines[key] = (observed.date(), tick.cumulative_volume,
                                         tick.cumulative_trade_value, signature, observed)
        self._ram_trade_sequence += 1
        start = received_at - self._ram_trade_retention_seconds
        while self._ram_trades and self._ram_trades[0]['available_at'] < start:
            self._ram_trades.popleft()
        if len(self._ram_trades) >= 10000:
            self._ram_trades.popleft()
            self._ram_observation_gap_at = now
        self._ram_trades.append({
            'input_ref': f'ram-0B:{self._ram_source_id}:{self._ram_observation_generation}:{self._ram_trade_sequence}',
            'sequence': self._ram_trade_sequence, 'code': key[0], 'market': key[1],
            'observed_at': observed.isoformat(), 'available_at': received_at,
            'price_won': price, 'volume': volume, 'trade_value_won': price*volume if volume is not None else None,
            'trade_value_basis': 'received_trade_price_x_volume',
            'cumulative_trade_value_delta_won': turnover,
            'cumulative_volume': tick.cumulative_volume,
            'cumulative_trade_value_million_won': tick.cumulative_trade_value,
            'input_resolution': '0B', 'source': 'kiwoom-websocket-0B',
        })

    def set_ram_observation_retention(self, seconds: int, *, owner=None) -> None:
        if self._ram_observation_owner is not None and self._ram_observation_owner is not owner:
            raise RuntimeError("Collector RAM observation lifetime already has an owner")
        if type(seconds) is not int or not 0 <= seconds <= 60:
            raise ValueError("RAM observation retention must be 0..60 seconds")
        previous = self._ram_trade_retention_seconds
        self._ram_trade_retention_seconds = seconds
        if owner is not None:
            self._ram_observation_owner = owner if seconds else None
        if seconds == 0:
            self._ram_retention_since = None
            self._ram_trades.clear()
            self._ram_trade_baselines.clear()
        elif self._ram_retention_since is None or seconds > previous:
            self._ram_retention_since = self._now_provider()

    def ram_observation_snapshot(self, codes: tuple[str, ...] | None, *, seconds: int,
                                 include_trade_references: bool = False, include_closed_minutes: bool = False) -> dict[str, Any]:
        """One event-loop RAM cut; no store read, drain, registration or aggregation."""
        at = self._now_provider()
        if at.tzinfo is None:
            raise ValueError("RAM observation clock must be timezone-aware")
        capture_anchor = self._ram_capture_anchor()
        if codes is None:
            codes = tuple(sorted({code for code, _ in self._approved_trade_sources}))
        if type(seconds) is not int or not 1 <= seconds <= 60 or len(set(codes)) > 200:
            raise ValueError("RAM observation cut/scope exceeds the budget")
        start = (at - timedelta(seconds=seconds)).timestamp()
        retained_start = (at - timedelta(seconds=self._ram_trade_retention_seconds)).timestamp()
        while self._ram_trades and self._ram_trades[0]['available_at'] < retained_start:
            self._ram_trades.popleft()
        rows = tuple(dict(row) for row in self._ram_trades if row['code'] in codes
                     and start <= datetime.fromisoformat(row['observed_at']).timestamp() <= at.timestamp()
                     and row['available_at'] <= at.timestamp())
        day = at.astimezone(timezone(timedelta(hours=9))).date().isoformat()
        self._ram_stock_references = {
            key: reference for key, reference in self._ram_stock_references.items()
            if key in self._approved_trade_sources and reference["trading_date"] == day
        }
        return {
            **({'closed_minute_observations':self._minute_bars.closed_observation_snapshot(codes,at=at,
                approved_sources=self._approved_trade_sources)} if include_closed_minutes else {}),
            **({'trade_references': tuple({**price, 'code':code, 'market':'KRX',
                    'market_cap_eok': self._latest_snapshots[('trade',code)]['event']['payload'].get('market_cap_eok')}
                for code in codes if (price := self.automation_price_observation(code)) is not None)}
               if include_trade_references else {}),
            "source_id": self._ram_source_id, "generation": self._ram_observation_generation,
            "decision_time": at.isoformat(), "rows": rows, "input_resolution": "0B",
            "approved_sources": tuple(sorted(self._approved_trade_sources)),
            "continuous_from": dict(self._continuous_from),
            "retention_since": self._ram_retention_since, "gap_at": self._ram_observation_gap_at,
            "stock_references": tuple(dict(value) for (code, _), value in sorted(self._ram_stock_references.items())
                                      if code in codes and value["available_at"] <= at.timestamp()),
            "durable": False,
            "capture_anchor": capture_anchor,
        }

    def _ram_capture_anchor(self) -> dict[str, Any] | None:
        """Reuse the recorder's accepted-input high water as an unverified hint.

        This never starts recording, reads the manifest, or promises that the
        current RAM values, excluded fields or earlier prefix were captured.
        """
        if self._input_replay_only:
            return None
        from .diagnostic_trace import input_token
        trace_id = input_token("collector_inputs")
        high_water = getattr(self, "_recorded_input_high_water", None)
        if (not trace_id or getattr(self, "_recorded_capture_epoch", None) != trace_id
                or type(high_water) is not tuple or len(high_water) != 2
                or high_water[0] != trace_id or type(high_water[1]) is not str
                or not 1 <= len(high_water[1]) <= 160):
            return None
        return {"trace_id": trace_id, "producer_component": f"realtime_collector:{id(self):x}",
                "input_id": high_water[1], "collector_input_version": COLLECTOR_INPUT_VERSION,
                "role": "last_accepted_collector_input", "verified": False}

    def _record_upstream(self, kind: str, payload: dict, *, excluded_types: dict | None = None) -> str | None:
        from . import diagnostic_trace as trace
        trace_id = trace.input_token("collector_inputs")
        if trace_id is None or self._input_replay_only:
            return
        fields = {"workload_id": "realtime", "producer_component": f"realtime_collector:{id(self):x}",
                  "input_kind": kind, "input_id": uuid4().hex, "excluded_types": excluded_types or {},
                  "collector_input_version": COLLECTOR_INPUT_VERSION,
                  "entered_mono_ns": time.monotonic_ns(), "entered_wall_ns": time.time_ns()}
        try:
            if getattr(self, "_recorded_capture_epoch", None) != trace_id:
                initial = {"source_time": self._now_provider(),
                           "approved_sources": tuple(sorted(self._approved_trade_sources)),
                           "continuous_from": tuple((code, market, at) for (code, market), at in
                                                    sorted(self._continuous_from.items())),
                           "initial_state": "cold_with_prefix",
                           "warm_accumulators": bool(self._pending_snapshots or self._minute_bars._dirty)}
                trace.emit_payload(trace_id, "collector_input", {
                    **fields, "input_kind": "initial_state", "input_id": uuid4().hex,
                }, initial)
                self._recorded_capture_epoch = trace_id
            if trace.emit_payload(trace_id, "collector_input", fields,
                                  {"source_time": self._now_provider(), **payload}):
                self._recorded_input_high_water = (trace_id, fields["input_id"])
                return fields["input_id"]
        except Exception:
            try:
                trace.reject_input(trace_id, {**fields, "reason": "collector_capture_error"})
            except Exception:
                pass

    def _record_upstream_message(self, message: dict[str, Any]) -> CapturedMessageReceipt | None:
        from . import diagnostic_trace as trace
        trace_id = trace.input_token("collector_inputs")
        if trace_id is None or self._input_replay_only or str(message.get("trnm", "")).upper() != "REAL":
            return
        receipt_enabled = trace.input_token("top20_inputs") == trace_id
        parents = []
        complete = True
        message_id = uuid4().hex
        try:
            rows = message.get("data")
            if type(rows) is not list or len(rows) > 10000:
                raise ValueError("collector_message_bounds")
            selected, excluded = [], {}
            offset = 0
            def flush_group():
                nonlocal offset, selected, excluded, complete
                identifier = self._record_upstream("message", {"message_id": message_id, "row_offset": offset,
                    "message": {"trnm": "REAL", "data": selected}}, excluded_types=excluded)
                if receipt_enabled:
                    if identifier:
                        parents.append(identifier)
                    else:
                        complete = False
                offset += len(selected)
                selected, excluded = [], {}
            for row in rows:
                if type(row) is not dict:
                    raise ValueError("collector_row_type")
                kind = row.get("type")
                if type(kind) is not str or len(kind) > 8:
                    raise ValueError("collector_event_type")
                fields = COLLECTOR_EVENT_FIELDS.get(kind)
                if fields is None:
                    label = kind if kind in {"0w", "0J", "0U", "00", "04"} else "other"
                    excluded[label] = excluded.get(label, 0) + 1
                    continue  # Never copy account fields or credentials.
                values = row.get("values")
                if type(values) is not dict:
                    raise ValueError("collector_values_type")
                selected.append({**{key: row[key] for key in ("item", "stk_cd", "code") if key in row},
                                 "type": kind, "values": {key: values[key] for key in fields
                                    if key in values}})
                if len(selected) == 100:
                    flush_group()
            if selected or excluded:
                flush_group()
        except Exception:
            complete = False
            try:
                trace.reject_input(trace_id, {"workload_id": "realtime",
                    "producer_component": f"realtime_collector:{id(self):x}",
                    "reason": "collector_message_not_captureable"})
            except Exception:
                pass
        if receipt_enabled:
            receipt = CapturedMessageReceipt(trace_id, f"realtime_collector:{id(self):x}",
                                             message_id, tuple(parents), complete)
            try:
                trace.emit(trace_id, "collector_message_receipt", {
                    "producer_component": receipt.producer_component, "message_id": message_id,
                    "parent_input_ids": receipt.input_ids, "complete": complete,
                })
            except Exception:
                return CapturedMessageReceipt(trace_id, receipt.producer_component, message_id,
                                              receipt.input_ids, False)
            return receipt

    def _publish_parsed(self, message: dict[str, Any]) -> None:
        message_receipt = self._record_upstream_message(message)
        for tick in parse_market_operation_ticks(message):
            from .diagnostic_top20_lifecycle_input import record_market_operation
            receipt = record_market_operation(self, tick)
            event = {
                "type": "market_operation",
                "payload": asdict(tick),
            }
            self._hub.publish(CapturedHubEvent(event, source=receipt) if receipt is not None else event)
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
            for ordinal, value in enumerate(parser(message)):
                if (
                    name == "trade" and self._subscription_filter_active
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
                delivered = event
                if message_receipt is not None and name in {"trade", "program_trade"}:
                    delivered = CapturedHubEvent(event, source=CapturedParserReceipt(message_receipt, name, ordinal))
                self._hub.publish(delivered, code)
                if name in {"trade", "market_state", "program_trade"}:
                    item_key = code or str(payload.get("market", ""))
                    snapshot = {
                        "event_type": name, "item_key": item_key,
                        "received_at": self._now_provider().timestamp(), "event": event,
                    }
                    self._pending_snapshots[(name, item_key)] = snapshot
                    self._latest_snapshots[(name, item_key)] = snapshot
                if name == "trade":
                    if self._automation_price_listener is not None:
                        observation = self.automation_price_observation(code)
                        if observation is not None:
                            try:
                                self._automation_price_listener(code, observation)
                            except Exception:
                                logger.exception('자동 모의운용 RAM 손절 가격 전달 실패')
                    self._observe_reconnect_trade()
                    now = self._now_provider()
                    received_at = now.timestamp()
                    if self._breakout_trade_listener is not None:
                        try:
                            self._breakout_trade_listener(payload, now,
                                source=f"{self._ram_source_id}:{self._ram_observation_generation}",
                                approved=(code, str(payload.get("market", "KRX"))) in self._approved_trade_sources)
                        except Exception:
                            logger.exception("0B 주도주 돌파 관측 전달 실패")
                    self._minute_bars.add(
                        value, now, received_at,
                        capture_complete=self._minute_capture_complete(value, now),
                    )
                    self._observe_ram_trade(value, now, received_at)
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
                    reference_key = (code, value.market)
                    if reference_key in self._approved_trade_sources and observed_at.tzinfo is not None:
                        day = observed_at.astimezone(timezone(timedelta(hours=9))).date().isoformat()
                        self._ram_stock_references[reference_key] = {
                            **payload, "trading_date": day, "available_at": observed_at.timestamp(),
                        }
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

    def set_automation_price_listener(self, listener):
        """One existing Supervisor, no subscriptions, queues, tasks or persistence."""
        self._automation_price_listener = listener

    def automation_price_observation(self, code: str):
        """Borrow the latest approved KRX trade; no seconds/DB/subscription work."""
        source = (code, 'KRX')
        row = self._latest_snapshots.get(('trade', code))
        if self._credential_paused or source not in self._approved_trade_sources or row is None:
            return None
        payload = row['event']['payload']
        at = datetime.fromtimestamp(row['received_at'], KST)
        since = self._continuous_from.get(source)
        if (payload.get('market') != 'KRX' or type(payload.get('current_price')) is not int
                or payload['current_price'] <= 0 or since is None or at < since
                or self._ram_observation_gap_at is not None and at < self._ram_observation_gap_at):
            return None
        return {'price_won': payload['current_price'], 'available_at': at,
                'input_ref': f'collector-trade:{self._ram_source_id}:{self._ram_observation_generation}:{code}:{row["received_at"]}',
                'market_status': 'TRADABLE', 'durable': False}

    def initial_realtime_snapshots(
        self, persisted: list[dict[str, Any]], codes: list[str],
    ) -> list[dict[str, Any]]:
        """Overlay current RAM values on the checkpoint sent to a new subscriber."""
        cutoff = self._now_provider().timestamp() - 300
        self._latest_snapshots = {
            key: snapshot for key, snapshot in self._latest_snapshots.items()
            if float(snapshot["received_at"]) > cutoff
        }
        requested = set(codes)
        live = {
            key: value for key, value in self._latest_snapshots.items()
            if key[0] == "market_state" or key[1] in requested
        }

        def key_for(event: dict[str, Any]) -> tuple[str, str]:
            payload = event.get("payload")
            values = payload if isinstance(payload, dict) else {}
            kind = str(event.get("type", ""))
            return kind, str(values.get("market" if kind == "market_state" else "code", ""))

        return [event for event in persisted if key_for(event) not in live] + [
            value["event"] for value in sorted(live.values(), key=lambda row: row["received_at"])
        ]

    def _deliver_account_event(self, event_type, value):
        if self._credential_paused or self._account_event_handler is None:
            return
        try:
            self._account_event_handler(event_type, value)
        except Exception:
            logger.warning("계좌 이벤트 전달 실패: %s", event_type)

    async def load_live_minute_bars(
        self, code: str, trading_date: str, market: str = "", *,
        start_minute: str | None = None, end_minute: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read persisted history plus a frozen copy of uncommitted RAM segments."""
        if self._store is None:
            return []
        deltas = [dict(value) for value in [
            *self._pending_minute_retries.values(), *self._inflight_minute_bars,
            *self._minute_bars.pending_bars(code, trading_date, market),
        ] if str(value['code']) == code and str(value['trading_date']) == trading_date
            and (not market or str(value['market']) == market)
            and (start_minute is None or str(value['minute']) >= start_minute)
            and (end_minute is None or str(value['minute']) < end_minute)]
        read_options = {}
        if start_minute is not None:
            read_options['start_minute'] = start_minute
        if end_minute is not None:
            read_options['end_minute'] = end_minute
        if deltas:
            read_options['realtime_deltas'] = deltas
        return await owned_to_thread(self._store.load_minute_bars, code, trading_date, market, **read_options)

    async def load_live_recent_minute_bars(
        self, code: str, start_date: str, end_date: str, market: str = "", trading_days: int = 2,
    ) -> list[dict[str, Any]]:
        """Freeze all requested RAM segments before the single owned native read."""
        if self._store is None:
            return []
        source_market = "" if market == "COMBINED" else market
        deltas = [dict(value) for value in [
            *self._pending_minute_retries.values(), *self._inflight_minute_bars,
            *self._minute_bars.pending_bars(code, end_date, source_market, start_date=start_date),
        ] if str(value['code']) == code and start_date <= str(value['trading_date']) <= end_date
            and (not source_market or str(value['market']) == source_market)]
        read_options = {'realtime_deltas': deltas} if deltas else {}
        return await owned_to_thread(
            self._store.load_recent_minute_bars, code, start_date, end_date, market, trading_days,
            **read_options,
        )

    async def _save_snapshots(self) -> None:
        next_latest = _next_latest_checkpoint(self._now_provider().timestamp())
        while True:
            await self._snapshot_sleep(1)
            latest_due = self._now_provider().timestamp() >= next_latest
            try:
                await self._flush_snapshots(flush_latest=latest_due, periodic=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 개별 저장 실패는 _flush_snapshots가 대기 자료를 복원한다.
                # 루프까지 종료되면 이후 모든 실시간 자료가 조용히 누락되므로
                # 다음 주기에 다시 시도한다.
                logger.exception("키움 중앙 실시간 DB 저장 실패; 다음 주기에 재시도합니다")
            else:
                if latest_due:
                    next_latest = _next_latest_checkpoint(self._now_provider().timestamp())
                    cutoff = self._now_provider().timestamp() - 300
                    self._latest_snapshots = {
                        key: value for key, value in self._latest_snapshots.items()
                        if float(value["received_at"]) > cutoff
                    }

    @captured_workload("realtime", "realtime_collector")
    async def _flush_snapshots(
        self, *, flush_latest: bool = True, periodic: bool = False, flush_seconds: bool = True,
    ) -> None:
        task = owned_create_task(self._flush_serialized(flush_latest, periodic, flush_seconds), name="realtime-owned-save")
        self._flush_tasks.add(task)
        task.add_done_callback(self._flush_tasks.discard)
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        await asyncio.shield(task)

    async def _flush_serialized(self, flush_latest: bool, periodic: bool, flush_seconds: bool) -> None:
        async with self._flush_lock:
            from .diagnostic_metrics import CURRENT_FLUSH_ID
            token = CURRENT_FLUSH_ID.set(uuid4().hex)
            try:
                await self._flush_snapshot_cycle(flush_latest, periodic, flush_seconds)
            finally:
                CURRENT_FLUSH_ID.reset(token)

    async def _flush_snapshot_cycle(
        self, flush_latest: bool = True, periodic: bool = False, flush_seconds: bool = True,
    ) -> None:
        if self._store is None:
            return
        cycle_now = self._now_provider()
        if flush_latest:
            values = list(self._pending_snapshots.values())
            self._pending_snapshots.clear()
            if values:
                try:
                    with self._observe_save("snapshots", values):
                        await owned_to_thread(self._store.save_realtime_snapshots, values)
                except Exception:
                    for value in values:
                        key = (str(value["event_type"]), str(value["item_key"]))
                        self._pending_snapshots.setdefault(key, value)
                    raise
        drained_minute_bars = self._minute_bars.drain_dirty(
            closed_before=cycle_now - timedelta(seconds=2) if periodic else None,
        )
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
                    now_provider=self._now_provider,
                )
                observations.append((bar_observation_key(observation), observation))
            self._inflight_minute_bars = minute_bars
            try:
                with self._observe_save("minute_bars", minute_bars):
                    await owned_to_thread(
                        self._store.save_minute_bars,
                        minute_bars,
                        observations=observations,
                    )
            except Exception:
                for value in minute_bars:
                    self._merge_minute_retry(value)
                raise
            finally:
                self._inflight_minute_bars = []
        entry_symbols = self._pending_account_entry_symbols
        entry_values = list(entry_symbols.values())
        self._pending_account_entry_symbols = {}
        if entry_values:
            try:
                with self._observe_save("account_entry_symbols", entry_values):
                    await owned_to_thread(
                        self._store.upsert_documents,
                        "account_entry_symbols_daily",
                        entry_values,
                    )
            except Exception:
                for value in entry_values:
                    self._pending_account_entry_symbols.setdefault(
                        (str(value["owner"]), str(value["key"])), value,
                    )
                raise
        reference_values = list(self._pending_stock_references.values())
        self._pending_stock_references.clear()
        if reference_values:
            try:
                with self._observe_save("stock_references", reference_values):
                    await owned_to_thread(
                        self._store.upsert_documents,
                        "stock_price_references",
                        reference_values,
                    )
            except Exception:
                for value in reference_values:
                    self._pending_stock_references.setdefault(str(value["owner"]), value)
                raise
        closures = list(self._pending_minute_finalizations.values())
        self._pending_minute_finalizations.clear()
        new_closures = self._minute_bars.drain_closed(cycle_now)
        finalized_at = self._now_provider().timestamp()
        for closure in new_closures:
            closure['available_at'] = finalized_at
        closures.extend(new_closures)
        if closures:
            try:
                with self._observe_save("minute_finalizations", closures):
                    await owned_to_thread(self._store.finalize_minute_bars, closures)
            except Exception:
                for value in closures:
                    key = tuple(
                        str(value[name])
                        for name in ("trading_date", "minute", "code", "market")
                    )
                    self._pending_minute_finalizations[key] = value
                raise
        market_history_by_subject = self._pending_market_history
        market_history = list(market_history_by_subject.values())
        self._pending_market_history.clear()
        if market_history:
            try:
                with self._observe_save("market_history", (
                    {"available_at": value[3].metadata.available_at} for value in market_history
                )):
                    await owned_to_thread(
                        self._store.save_dataset_snapshots,
                        [
                            ("market_state", subject, snapshot_key, payload, observation)
                            for subject, snapshot_key, payload, observation in market_history
                        ],
                    )
            except Exception:
                for retry_value in market_history:
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

    def _subscription_groups(
        self, session, codes, nxt_codes, program_codes=None, priority_codes=None,
    ):
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
            {"item": [""], "type": ["0s"]},
            *([{"item": [], "type": ["1h"]}]
              if session == "KRX" and self._market_events is not None else []),
        ]
        return groups

    @captured_subscription
    async def _send_subscription(
        self, websocket: Any, session: str, codes: tuple[str, ...], nxt_codes: tuple[str, ...],
        program_codes: tuple[str, ...] | None = None,
        priority_codes: tuple[str, ...] | None = None,
        *, previous_groups: dict[str, list[dict[str, object]]] | None = None,
    ) -> dict[str, list[dict[str, object]]]:
        # Explicit v1 historical policy; live _receive uses the incremental planner.
        groups = self._subscription_groups(session, codes, nxt_codes, program_codes, priority_codes)
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
            self._boundary_task = owned_create_task(
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
