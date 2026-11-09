"""NAS가 데스크톱 앱과 무관하게 TOP20 시장 자료를 수집한다."""

from __future__ import annotations

import asyncio
from .async_lifetime import await_owned_task, owned_create_task, owned_to_thread
import logging
import time
from dataclasses import asdict
from contextlib import asynccontextmanager, nullcontext
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Protocol, Sequence

from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
from kiwoom_monitor.application.program_trade_service import program_trade_rows
from kiwoom_monitor.application.stock_enrichment import (
    NXT_CATALOG_KIND, load_excluded_product_codes, load_premarket_catalog, nxt_catalog_refresh_due,
)
from kiwoom_monitor.application.market_session_schedule import (
    KRX_AFTER_MARKET_EFFECTIVE_DATE,
    full_day_close_at,
)
from kiwoom_monitor.application.historical_high_service import HistoricalHighService
from kiwoom_monitor.application.daily_bar_coverage import (
    COLLECTION as DAILY_HISTORY_COLLECTION, DailySourceWindow, assess_daily_coverage, choose_daily_coverage,
    fingerprint,
)
from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord, Top20TradeValueCollector
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    MarketOperationTick, ProgramTradeTick, TradeTick,
)
from kiwoom_monitor.infrastructure.krx.stock_catalog import fetch_krx_stock_catalog
from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.domain.market_data_contract import MarketDataObservation
from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
from kiwoom_monitor.domain.top20_reconciliation import (
    BAR_FIELDS, RECONCILED_KIND, Top20ReconciliationInput, bar_fingerprint,
    build_top20_reconciliation, metadata_fingerprint,
)
from kiwoom_monitor.infrastructure.market_data_metadata_codec import market_metadata_document
from kiwoom_monitor.application.ranking_schedule import ranking_history_due, ranking_snapshot_archive_due

from .market_ingest import (
    fundamentals_document_is_current, fundamentals_refresh_key,
    parse_minute_response,
)
from .market_observations import ranking_observation, top20_index_observation
from .persistent_outbox import JsonRecordOutbox
from .program_snapshot_writer import ProgramSnapshotWriter
from .realtime_hub import RealtimeHub, RealtimeSubscriber
from .rest_broker import CentralRestBroker

if TYPE_CHECKING:
    from .listing_evidence import KindListingEvidence
    from .program_flow_archive import ProgramFlowArchive


logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class AutonomousTop20Store(Protocol):
    """TOP20 순위·준비·지수·프로그램 자료의 조회 및 native 저장 계약."""

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]: ...

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...


    def load_daily_bars(
        self, code: str, market: str = "", limit: int = 250,
    ) -> list[dict[str, Any]]: ...

    def load_minute_bars(
        self, code: str, trading_date: str, market: str = "", *,
        realtime_deltas: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]: ...

    def load_dataset_snapshots(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def load_top20_reconciliation_inputs(self, trading_date: str) -> Top20ReconciliationInput: ...

    def load_market_data_metadata_range(self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime) -> list[Any]: ...

    def load_observation_revisions(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any], *,
        observation: MarketDataObservation[object] | None = None,
    ) -> None: ...

    def save_dataset_snapshots(
        self,
        values: list[tuple[
            str, str, str, dict[str, Any], MarketDataObservation[object] | None,
        ]],
    ) -> None: ...


class DailyReferenceState:
    """Own daily input revisions, accepted reference cuts and history borrowers."""

    def __init__(self) -> None:
        # Native ingestion only invalidates versions; asyncio leases stay on the loop.
        self._daily_input_lock = Lock()
        self._daily_input_versions: dict[tuple[str, str], int] = {}
        self._daily_verified_versions: dict[tuple[str, str], int] = {}
        self._daily_stage_versions: dict[tuple[str, str], tuple[int, int]] = {}
        self._daily_history_locks: dict[tuple[str, str], tuple[asyncio.Lock, int]] = {}

    def _versions(self, code: str) -> tuple[int, int]:
        """Read a cut while the caller holds the input lock."""
        return tuple(self._daily_input_versions.get((code, market), 0) for market in ("KRX", "NXT"))

    def invalidate(self, code: str, market: str) -> None:
        """Mark committed changes or uncertain saves without scheduling work."""
        with self._daily_input_lock:
            key = (code, market)
            self._daily_input_versions[key] = self._daily_input_versions.get(key, 0) + 1

    def source_versions(self, code: str) -> tuple[int, int]:
        with self._daily_input_lock:
            return self._versions(code)

    def market_is_current(self, code: str, market: str) -> bool:
        with self._daily_input_lock:
            key = (code, market)
            return self._daily_verified_versions.get(key, 0) == self._daily_input_versions.get(key, 0)

    def accept_market(self, code: str, market: str, expected_version: int) -> None:
        with self._daily_input_lock:
            key = (code, market)
            if self._daily_input_versions.get(key, 0) != expected_version:
                raise RuntimeError(f"{market} 일봉 검증 중 입력이 변경되었습니다.")
            self._daily_verified_versions[key] = expected_version

    def accept_daily(self, code: str, markets: Sequence[str]) -> None:
        with self._daily_input_lock:
            for market in markets:
                key = (code, market)
                if self._daily_verified_versions.get(key, 0) != self._daily_input_versions.get(key, 0):
                    raise RuntimeError(f"{market} 일봉 검증 중 입력이 변경되었습니다.")
            self._daily_stage_versions[(code, "daily")] = self._versions(code)

    def accept_high(self, code: str, expected_versions: tuple[int, int]) -> None:
        with self._daily_input_lock:
            if expected_versions != self._versions(code):
                raise RuntimeError("신고가 계산 중 일봉 입력이 변경되었습니다.")
            self._daily_stage_versions[(code, "high")] = expected_versions

    def stage_is_current(self, code: str, stage: str) -> bool:
        with self._daily_input_lock:
            return self._daily_stage_versions.get((code, stage), (0, 0)) == self._versions(code)

    @asynccontextmanager
    async def history_lease(self, code: str, market: str) -> AsyncIterator[None]:
        # Waiters own a reference too: cancellation cannot replace another owner's lock.
        key = (code, market)
        lock, users = self._daily_history_locks.get(key, (asyncio.Lock(), 0))
        self._daily_history_locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self._daily_history_locks[key]
            if users == 1:
                del self._daily_history_locks[key]
            else:
                self._daily_history_locks[key] = (lock, users - 1)


class Top20AfterCloseWorkflow:
    """Own the after-close day, retry progress, entrants and actual task lifetime."""

    def __init__(
        self, store: AutonomousTop20Store, *, now_provider: Callable[[], datetime],
        minute_backfill_enabled: bool, capture_identity: str,
        backfill_market_indexes: Callable[[str], Awaitable[None]],
        nxt_enabled: Callable[[str, str], Awaitable[bool | None]],
        backfill_daily: Callable[[str, str, str], Awaitable[None]],
        backfill_minutes: Callable[[str, str, str], Awaitable[None]],
        backfill_candidate_flows: Callable[[str, str], Awaitable[None]],
        load_daily_entrant_state: Callable[..., Awaitable[tuple[dict[str, str], set[str]]]],
        persist_missing_daily_entrants: Callable[[str, dict[str, str], set[str]], Awaitable[None]],
        is_observed_krx_trading_day: Callable[[str], Awaitable[bool]],
        previous_observed_krx_trading_day: Callable[[datetime], Awaitable[str]],
        capture_scope: Callable[..., Any] | None = None,
        workload_paused: Callable[[str], bool] | None = None,
        archive_program_flow: Callable[[str], Awaitable[None]] | None = None,
        archive_observations: Callable[[str, datetime], Awaitable[None]] | None = None,
        filter_after_close_codes: Callable[[tuple[str, ...], str], Awaitable[tuple[str, ...]]] | None = None,
    ) -> None:
        self._store = store
        self._now = now_provider
        self._capture_identity = capture_identity
        self._capture_scope = capture_scope
        self._workload_paused = workload_paused or (lambda _workload: False)
        self._minute_backfill_enabled = minute_backfill_enabled
        self._backfill_market_indexes = backfill_market_indexes
        self._nxt_enabled = nxt_enabled
        self._backfill_daily = backfill_daily
        self._backfill_minutes = backfill_minutes
        self._backfill_candidate_flows = backfill_candidate_flows
        self._load_daily_entrant_state = load_daily_entrant_state
        self._persist_missing_daily_entrants = persist_missing_daily_entrants
        self._is_observed_krx_trading_day = is_observed_krx_trading_day
        self._previous_observed_krx_trading_day = previous_observed_krx_trading_day
        self._archive_program_flow = archive_program_flow
        self._archive_observations = archive_observations
        self._filter_after_close_codes = filter_after_close_codes
        self._last_backfill_day = ""
        self._backfill_task: asyncio.Task[bool] | None = None
        self._backfill_retry_day = ""
        self._backfill_retry_at = 0.0
        self._backfill_attempts = 0
        self._backfill_progress_day = ""
        self._backfill_completed_steps: dict[str, set[str]] = {}
        self._backfill_entrant_first_seen: dict[str, str] | None = None
        self._backfill_entrant_persisted_codes: set[str] = set()
        self._calendar_unknown_logged_days: set[str] = set()

    def request_stop(self) -> None:
        if self._backfill_task is not None:
            self._backfill_task.cancel()

    async def join(self) -> None:
        task = self._backfill_task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def consider(self, now: datetime) -> None:
        if now.weekday() < 5 and now.time().replace(tzinfo=None) >= clock_time(20, 5):
            day = now.date().isoformat()
            if self._last_backfill_day == day:
                return
            if await self._is_observed_krx_trading_day(day):
                self.schedule(day)
            elif day not in self._calendar_unknown_logged_days:
                self._calendar_unknown_logged_days.add(day)
                logger.info(
                    "KRX 장후 보완 대기: %s의 0s 거래일 증거가 없어 종목별 TR을 시작하지 않습니다",
                    day,
                )
        elif now.weekday() < 5 and clock_time(7) <= now.time().replace(tzinfo=None) < clock_time(7, 40):
            day = await self._previous_observed_krx_trading_day(now)
            if day and self._last_backfill_day != day:
                self.schedule(day)

    async def run_day(self, day: str) -> bool:
        with (self._capture_scope(self, "top20", "autonomous_top20",
                producer_identity=lambda workflow: workflow._capture_identity)
              if self._capture_scope is not None else nullcontext()):
            target_day = datetime.fromisoformat(day).date()
            now = self._now()
            close_at = full_day_close_at(target_day, venue="KRX")
            if (
                target_day >= KRX_AFTER_MARKET_EFFECTIVE_DATE
                and target_day == now.date()
                and (close_at is None or now.time().replace(tzinfo=None) < close_at.time().replace(tzinfo=None))
            ):
                logger.info("KRX 전체일 차트 보완 대기: %s (20:00 종료 전)", day)
                return False
            if self._backfill_progress_day != day:
                self._backfill_progress_day = day
                self._backfill_completed_steps.clear()
                self._backfill_entrant_first_seen = None
                self._backfill_entrant_persisted_codes = set()
            succeeded = True
            try:
                await self._backfill_market_indexes(day)
            except Exception as error:
                succeeded = False
                logger.warning("시장지수 장후 보완 실패: %s %s", day, error)
            documents = await owned_to_thread(self._store.load_documents, "top20_daily_entrants", day, 5000)
            if len(documents) >= 5000:
                raise RuntimeError(f"TOP20 daily entrant recovery limit reached: {day}")
            if self._backfill_entrant_first_seen is None:
                first_seen, persisted = await self._load_daily_entrant_state(day, documents)
                self._backfill_entrant_first_seen = first_seen
                self._backfill_entrant_persisted_codes = persisted
            first_seen = self._backfill_entrant_first_seen
            persisted = self._backfill_entrant_persisted_codes
            # Re-read documents on every retry so newly admitted symbols join the backfill.
            persisted.update(str(row["key"]).strip() for row in documents if row.get("key"))
            account_entries = await owned_to_thread(
                self._store.load_documents, "account_entry_symbols_daily", day, 5000,
            )
            codes = tuple(dict.fromkeys([
                *first_seen,
                *(str(value.get("key", "")) for value in documents if value.get("key")),
                *(str(value.get("key", "")) for value in account_entries if value.get("key")),
            ]))
            if self._filter_after_close_codes is not None:
                codes = await self._filter_after_close_codes(codes, day)
            allowed = set(codes)
            await self._persist_missing_daily_entrants(
                day, {code: seen for code, seen in first_seen.items() if code in allowed}, persisted,
            )
            for code in codes:
                if self._now().time().replace(tzinfo=None) >= clock_time(7, 40) and self._now().date().isoformat() != day:
                    succeeded = False
                    break
                completed = self._backfill_completed_steps.setdefault(code, set())
                if "bars" not in completed:
                    try:
                        from .postgres_access import db_call_source
                        if "krx_bars" not in completed:
                            await self._backfill_daily(code, day, "KRX")
                            with db_call_source("top20.after_close_minutes"):
                                await self._backfill_minutes(code, day, "KRX")
                            if self._minute_backfill_enabled and not self._workload_paused("minute_backfill"):
                                completed.add("krx_bars")
                        eligible = await self._nxt_enabled(code, day)
                        if eligible:
                            await self._backfill_daily(code, day, "NXT")
                            with db_call_source("top20.after_close_minutes"):
                                await self._backfill_minutes(code, day, "NXT")
                        if eligible is not None and "krx_bars" in completed and not self._workload_paused("minute_backfill"):
                            completed.add("bars")
                        else:
                            succeeded = False
                    except Exception as error:
                        succeeded = False
                        logger.warning("TOP20 편입종목 장후 보완 실패: %s %s", code, error)
                if "flows" not in completed:
                    try:
                        await self._backfill_candidate_flows(code, day)
                        completed.add("flows")
                    except Exception as error:
                        succeeded = False
                        logger.warning("TOP20 편입종목 장후 수급 보완 실패: %s %s", code, error)
            if not self._workload_paused("minute_backfill") and not self._workload_paused("top20_after_close"):
                try:
                    await self.reconcile(day)
                except Exception as error:
                    succeeded = False
                    logger.warning("TOP20 장후 재계산 실패(재시도): %s %s", day, error)
            completed_day = succeeded and not self._workload_paused("minute_backfill") and not self._workload_paused("top20_after_close")
            if completed_day:
                completed_at = as_kst(self._now())
                if self._archive_program_flow is not None:
                    if completed_at.date() == target_day and completed_at.time() >= clock_time(20, 5):
                        # Only the local freeze is part of this completion. NAS I/O runs independently.
                        await self._archive_program_flow(day)
                if self._archive_observations is not None:
                    await self._archive_observations(day, completed_at)
                self._backfill_progress_day = ""
                self._backfill_completed_steps.clear()
            return completed_day


    async def reconcile(self, day: str) -> dict[str, Any] | None:
        """Publish one derived day only after the full-day collection window closes."""
        if self._workload_paused("top20_after_close") or self._workload_paused("minute_backfill"):
            return None
        now = self._now()
        if day > now.date().isoformat() or (day == now.date().isoformat() and now.hour < 20):
            raise RuntimeError("TOP20 전체일 재계산은 20:00 종료 뒤에만 가능합니다.")
        inputs = await owned_to_thread(self._store.load_top20_reconciliation_inputs, day)
        payload = await owned_to_thread(build_top20_reconciliation, inputs, available_at=self._now().isoformat())
        previous = await owned_to_thread(self._store.load_dataset_snapshots, RECONCILED_KIND, day, 1)
        if previous and previous[0].get("payload", {}).get("generation") == payload["generation"]:
            return previous[0]["payload"]
        if self._workload_paused("top20_after_close") or self._workload_paused("minute_backfill"):
            return None
        # The generic native writer owns the single commit and cache invalidation.
        publication = owned_create_task(
            owned_to_thread(self._store.save_dataset_snapshot, RECONCILED_KIND, day, day, payload),
            name=f"nas-top20-reconciliation-publish-{day}",
        )
        try:
            await asyncio.shield(publication)
        except asyncio.CancelledError:
            # The existing backfill owner remains alive until its native write finishes;
            # close() joins that owner before the app closes the selected store.
            await publication
            raise
        logger.info("TOP20 장후 재계산 게시: %s complete=%s/720 generation=%s", day,
                    payload["coverage"]["full_day"]["complete"], payload["generation"])
        return payload


    def schedule(self, day: str) -> None:
        if self._workload_paused("top20_after_close"):
            return
        task = self._backfill_task
        if task is not None and not task.done():
            return
        if self._backfill_retry_day == day and time.monotonic() < self._backfill_retry_at:
            return
        task = owned_create_task(
            self.run_day(day), name=f"nas-top20-backfill-{day}",
        )
        self._backfill_task = task
        task.add_done_callback(self._done)


    def _done(self, task: asyncio.Task[bool]) -> None:
        if self._backfill_task is task:
            self._backfill_task = None
        if task.cancelled():
            return
        error = task.exception()
        day = task.get_name().removeprefix("nas-top20-backfill-")
        if error is None and task.result():
            self._last_backfill_day = day
            self._backfill_retry_day = ""
            self._backfill_attempts = 0
            return
        attempts = self._backfill_attempts + 1 if self._backfill_retry_day == day else 1
        self._backfill_retry_day = day
        self._backfill_attempts = attempts
        delay = min(300, 30 * 2 ** (attempts - 1))
        self._backfill_retry_at = time.monotonic() + delay
        logger.warning("TOP20 장후 보완 미완료: %s; %d초 후 재시도; error=%s", day, delay, error)



class AutonomousTop20Service:
    """24시간 순위와 08:00~20:00 0B 수집, 장후 차트 보완을 담당한다."""

    STALE_RANKING_RETRY_LIMIT = 20

    def __init__(
        self, broker: CentralRestBroker, hub: RealtimeHub, store: AutonomousTop20Store,
        *, now_provider: Callable[[], datetime] | None = None,
        catalog_loader: Callable[[], tuple[tuple[str, str, str], ...]] = fetch_krx_stock_catalog,
        outbox_path: Path | None = None,
        minute_backfill_enabled: bool = True,
        on_membership_changed: Callable[[], None] | None = None,
        capture_scope: Callable[..., Any] | None = None,
        ranking_input_observer: Any = None,
        flow_input_observer: Any = None,
        catalog_input_observer: Any = None,
        workload_paused: Callable[[str], bool] | None = None,
        program_flow_archive: ProgramFlowArchive | None = None,
        full_day_halt: Callable[[str, date], dict[str, str] | None] | None = None,
        stock_master_required: bool = False,
        listing_evidence: KindListingEvidence | None = None,
    ) -> None:
        self._broker = broker
        self._hub = hub
        self._store = store
        self._now = now_provider or (lambda: datetime.now(KST))
        self._catalog_loader = catalog_loader
        self._capture_scope = capture_scope
        self._ranking_input_observer = ranking_input_observer
        self._flow_input_observer = flow_input_observer
        self._catalog_input_observer = catalog_input_observer
        self._workload_paused = workload_paused or (lambda _workload: False)
        self._minute_backfill_enabled = minute_backfill_enabled
        self._full_day_halt = full_day_halt
        self._stock_master_required = stock_master_required
        self._listing_evidence = listing_evidence
        self._excluded_product_codes: frozenset[str] | None = None
        self._stock_master_day = ""
        self._stock_master_retry_at = 0.0
        self._stock_master_lock = asyncio.Lock()
        self._on_membership_changed = on_membership_changed
        if not minute_backfill_enabled:
            logger.info("NAS TOP20 자동 분봉 백필 비활성화: ka10080 보완 조회를 건너뜁니다.")
        self._outbox = JsonRecordOutbox(outbox_path) if outbox_path is not None else None
        self._subscriber: RealtimeSubscriber | None = None
        self._observed_dropped_events = 0
        self._tasks: list[asyncio.Task[None]] = []
        self._collector = Top20TradeValueCollector()
        self._collector_lock = asyncio.Lock()
        self._trade_values = MinuteTradeValueAggregator(max_minutes=1_440)
        self._markets: dict[str, str] = {}
        self._market_catalog_day = ""
        self._nxt_catalogs: dict[str, dict[str, bool] | None] = {}
        self._premarket_catalog_day = ""
        self._premarket_markets: dict[str, str] = {}
        self._nxt_catalog_lock = asyncio.Lock()
        self._nxt_catalog_task: asyncio.Task[None] | None = None
        self._nxt_catalog_retry_at = 0.0
        self._fundamentals_ready: dict[str, str] = {}
        # Success belongs to each stock/day/stage; aggregate flags are summaries only.
        self._entry_stage_ready: dict[tuple[str, str], str] = {}
        self._entry_data_ready: dict[str, str] = {}
        self._fundamentals_pending: set[str] = set()
        self._fundamentals_tasks: set[asyncio.Task[None]] = set()
        self.daily_reference = DailyReferenceState()
        self._market_catalog_task: asyncio.Task[None] | None = None
        self._subscription_task: asyncio.Task[None] | None = None
        self._subscription_revision = 0
        self._subscription_day = ""
        self._last_ranking_slot = ""
        self._last_aux_ranking_slots: dict[str, str] = {}
        self._krx_trading_day_cache: dict[str, tuple[float, bool]] = {}
        self._entrants_day = ""
        self._entrant_first_seen: dict[str, str] = {}
        self._entrant_persisted_codes: set[str] = set()
        self._account_entry_codes: tuple[str, ...] = ()
        self._pending_index_records: dict[str, dict[str, Any]] = {}
        self._index_outbox_lock = asyncio.Lock()
        self._index_flush_lock = asyncio.Lock()
        self._index_save_task: asyncio.Task[None] | None = None
        self._program_snapshots = ProgramSnapshotWriter(store)
        self._program_flow_archive = program_flow_archive
        self._pending_program_gaps: dict[tuple[str, str], dict[str, Any]] = {}
        self._program_gap_lock = asyncio.Lock()
        self._program_ready_codes: set[str] = set()
        self._program_outage: tuple[datetime, set[str]] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._latest_membership_snapshot: dict[str, Any] | None = None
        self.after_close = Top20AfterCloseWorkflow(
            store, now_provider=self._now, minute_backfill_enabled=minute_backfill_enabled,
            capture_identity=f"autonomous_top20:{id(self):x}",
            backfill_market_indexes=self._backfill_market_indexes,
            nxt_enabled=self._nxt_enabled, backfill_daily=self._backfill_daily,
            backfill_minutes=self._backfill_minutes,
            backfill_candidate_flows=self._backfill_candidate_flows,
            load_daily_entrant_state=self._load_daily_entrant_state,
            persist_missing_daily_entrants=self._persist_missing_daily_entrants,
            is_observed_krx_trading_day=self._is_observed_krx_trading_day,
            previous_observed_krx_trading_day=self._previous_observed_krx_trading_day,
            capture_scope=capture_scope,
            workload_paused=workload_paused,
            archive_program_flow=self._freeze_program_flow if program_flow_archive is not None else None,
            archive_observations=self._freeze_observation_day if program_flow_archive is not None else None,
            filter_after_close_codes=self._filter_after_close_codes if stock_master_required else None,
        )

    async def _freeze_program_flow(self, day: str) -> None:
        await self._program_snapshots.drain()
        await self._program_flow_archive.freeze_day(day, self._now(),
                                                    excluded_codes=self._excluded_product_codes or frozenset())

    async def _freeze_observation_day(self, day: str, completed_at: datetime) -> None:
        await self._program_flow_archive.freeze_observations(day, completed_at,
                                                             excluded_codes=self._excluded_product_codes or frozenset())

    def stock_enrichment_codes(self, codes: Sequence[str], *, day: str = "") -> tuple[str, ...]:
        if not self._stock_master_required:
            return tuple(codes)
        excluded = self._excluded_product_codes
        if excluded is None or self._stock_master_day < (day or self._now().date().isoformat()):
            return ()
        return tuple(code for code in codes if normalize_stock_code(code) not in excluded)

    async def _filter_after_close_codes(self, codes: tuple[str, ...], day: str) -> tuple[str, ...]:
        if codes:
            await self._ensure_stock_master(day)
            if self._excluded_product_codes is None or self._stock_master_day < day:
                raise RuntimeError("product list unavailable for after-close stock backfill")
        return self.stock_enrichment_codes(codes, day=day)

    async def _ensure_stock_master(self, day: str) -> None:
        async with self._stock_master_lock:
            if self._stock_master_day >= day or time.monotonic() < self._stock_master_retry_at:
                return
            adapter = _AsyncBrokerChartAdapter(self._broker, asyncio.get_running_loop())
            try:
                codes = await await_owned_task(owned_create_task(owned_to_thread(
                    load_excluded_product_codes, adapter.request_with_continuation,
                ), name="top20-product-list"))
            except Exception as error:
                self._stock_master_retry_at = time.monotonic() + 300
                logger.warning("키움 ETF·ETN·ELW 제외 목록 갱신 실패: %s", type(error).__name__)
                return
            self._excluded_product_codes = codes
            self._stock_master_day = max(day, self._now().date().isoformat())

    def latest_membership_snapshot(
        self, subject: str = "", *, include_memory_only: bool = True,
    ) -> dict[str, Any] | None:
        """Return the latest validated 20-slot projection without waiting for persistence."""
        value = self._latest_membership_snapshot
        if (value is None or (subject and str(value["subject"]) != subject)
                or (not include_memory_only and value.get("persistence_state") == "memory_only")):
            return None
        payload = value["payload"]
        return {
            **value,
            "payload": {
                **payload,
                "codes": list(payload["codes"]),
                "items": [dict(item) for item in payload["items"]],
            },
        }

    async def start(self) -> None:
        if self._close_task is not None:
            await asyncio.shield(self._close_task)
            self._close_task = None
        if self._tasks:
            return
        if self._outbox is not None:
            self._pending_index_records.update(await owned_to_thread(self._outbox.load))
            if self._pending_index_records:
                try:
                    await self._flush_pending_indexes()
                except Exception as error:
                    logger.warning("NAS TOP20 영속 재시도 복구 실패(계속 실행): %s", error)
        self._subscriber = self._hub.connect(capture_component=f"autonomous_top20:{id(self):x}")
        self._observed_dropped_events = self._subscriber.dropped_events
        self._tasks = [
            owned_create_task(self._schedule_loop(), name="nas-top20-schedule"),
            owned_create_task(self._event_loop(), name="nas-top20-events"),
            owned_create_task(self._index_loop(), name="nas-top20-index"),
            owned_create_task(self._persistence_loop(), name="nas-top20-persistence"),
        ]
        if self._program_flow_archive is not None:
            self._program_flow_archive.start()

    async def close(self) -> None:
        # Cancelling a shutdown waiter must not abandon an actual DB write.
        if self._close_task is None or (
            self._close_task.done() and self._close_task.exception() is not None
        ):
            self._close_task = owned_create_task(self._close(), name="nas-top20-close", shutdown=True)
            self._close_task.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None,
            )
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        self.after_close.request_stop()
        for task in self._fundamentals_tasks:
            task.cancel()
        for task in self._tasks:
            task.cancel()
        for task in tuple(self._fundamentals_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.after_close.join()
        if self._program_flow_archive is not None:
            await self._program_flow_archive.close()
        self._fundamentals_tasks.clear()
        self._fundamentals_pending.clear()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        if self._subscriber is not None:
            self._hub.disconnect(self._subscriber)
            self._subscriber = None
        self._finish_program_outage(self._now())
        self._program_ready_codes.clear()
        # A cancelled persistence waiter must not outlive its actual save owner.
        # Failed/newer index input stays in the outbox for the existing start recovery.
        if self._index_save_task is not None:
            try:
                await asyncio.shield(self._index_save_task)
            except Exception as error:
                logger.warning("NAS TOP20 지수 종료 저장 실패(영속 재시도 보존): %s", error)
        await self._program_snapshots.drain()
        await self._persist_program_gaps()

    async def _select_ranking_response(self, now: datetime):
        """Shared live/offline ranking validation; no subscription or store effects."""
        expected_at = _ranking_expected_at(now)
        items: list[dict[str, Any]] = []
        result = None
        stale_response = False
        partial_response = False
        request = getattr(self._broker, "request_unrecorded", self._broker.request)
        for retry_index in range(self.STALE_RANKING_RETRY_LIMIT + 1):
            # Freshness/20-slot validation comes before persistence. Persisting every
            # stale or partial candidate here used to block the next retry on the
            # PostgreSQL ingest path and made the desktop ranking appear seconds late.
            if self._ranking_input_observer is None:
                result = await request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
            else:
                result = await self._ranking_input_observer.ranking_request(request, retry_index)
            raw_items = result.payload.get("item_inq_rank", result.payload.get("result_list", []))
            items = [row for row in raw_items if isinstance(row, dict)] if isinstance(raw_items, list) else []
            snapshot_at = _ranking_snapshot_at(items, result.payload)
            stale_response = snapshot_at is not None and snapshot_at < expected_at
            partial_response = _ranking_response_has_empty_slots(items)
            if not stale_response and not partial_response:
                break
            if retry_index >= self.STALE_RANKING_RETRY_LIMIT:
                break
            delay = _stale_ranking_retry_delay(retry_index)
            if stale_response:
                logger.info(
                    "NAS ka00198이 이전 기준 스냅샷(%s)을 반환해 %.2f초 뒤 재조회합니다. (%d/%d)",
                    snapshot_at.strftime("%H:%M:%S"), delay,
                    retry_index + 1, self.STALE_RANKING_RETRY_LIMIT,
                )
            else:
                valid_count = sum(
                    bool(_stock_code(row) and str(row.get("stk_nm", "")).strip())
                    for row in items
                )
                logger.info(
                    "NAS ka00198 최신 응답이 부분 자료(%d/20)여서 %.2f초 뒤 재조회합니다. (%d/%d)",
                    valid_count, delay,
                    retry_index + 1, self.STALE_RANKING_RETRY_LIMIT,
                )
            await asyncio.sleep(delay)
        assert result is not None
        return result, items, stale_response, partial_response

    async def refresh_ranking_once(self, observed_at: datetime | None = None) -> tuple[str, ...]:
        now = observed_at or self._now()
        observer = self._ranking_input_observer
        with observer.ranking_refresh_scope(self, now) if observer is not None else nullcontext():
            refresh_started_at = time.monotonic()
            history_due = ranking_history_due(now)
            if history_due:
                self._schedule_market_catalog(now.date().isoformat())
            result, items, stale_response, partial_response = await self._select_ranking_response(now)
            if stale_response or partial_response:
                logger.warning(
                    "recording_gap: NAS TOP20 최신 완성 순위를 제한 시간 안에 받지 못해 이번 회차를 저장하지 않습니다."
                )
                return tuple(self._collector.active_codes)
            codes = tuple(dict.fromkeys(filter(None, (_stock_code(row) for row in items))))[:20]
            snapshot_key = _ranking_snapshot_key(items, now, result.payload)
            day = snapshot_key[:10]
            history_due = history_due and ranking_history_due(datetime.fromisoformat(snapshot_key))
            membership = {
                "observed_at": snapshot_key, "codes": list(codes), "items": items,
            }
            # Only a fresh, complete 20-slot response reaches this point. Publish
            # that validated projection before PostgreSQL persistence so the
            # desktop's live limit=1 read is not delayed by storage latency.
            projection_time = self._now()
            history_due = history_due and ranking_history_due(projection_time)
            if projection_time.tzinfo is None:
                projection_time = projection_time.replace(tzinfo=KST)
            projection_saved_at = projection_time.timestamp()
            self._latest_membership_snapshot = {
                "subject": day,
                "snapshot_key": snapshot_key,
                "saved_at": projection_saved_at,
                "payload": membership,
                "persistence_state": "pending" if history_due else "memory_only",
            }
            if not history_due:
                return codes
            enrichment_codes = self.stock_enrichment_codes(codes)
            # The validated live projection and its KRX 0B subscription use the
            # same in-memory codes. Neither waits for membership/entrant/index DB
            # writes; the websocket still reports readiness only after REG ACK.
            async with self._collector_lock:
                # REST retries and lock waits can outlive the request's ranking slot.
                # Sampling at that old time rewinds the collector and fabricates a
                # >2s gap on its next advance despite uninterrupted index sampling.
                collection_now = self._now()
                if not ranking_history_due(collection_now):
                    self._latest_membership_snapshot["persistence_state"] = "memory_only"
                    return codes
                update = self._advance(collection_now)
                self._collector.prepare(
                    enrichment_codes, collection_now, enabled=True,
                    collection_open=_collection_open(collection_now),
                )
            if self._on_membership_changed is not None:
                try:
                    self._on_membership_changed()
                except Exception:
                    logger.exception("validated TOP20 projection notification failed")
            self._schedule_subscription_update(day)
            # The collector has already moved to the next minute. Preserve its
            # completed record before any unrelated membership/entrant DB write.
            if update.completed is not None:
                await self._queue_index(update.completed)
            persistence_started_at = time.monotonic()
            # Keep the schedule owner alive until its accepted native write finishes.
            await await_owned_task(owned_create_task(
                owned_to_thread(
                    self._store.save_dataset_snapshot,
                    "top20_membership",
                    day,
                    snapshot_key,
                    membership,
                    observation=ranking_observation(
                        day,
                        snapshot_key,
                        membership,
                        self._now(),
                        source="nas-autonomous-ka00198",
                    ),
                ),
                name="nas-top20-membership-save",
            ))
            if (
                self._latest_membership_snapshot is not None
                and self._latest_membership_snapshot["snapshot_key"] == snapshot_key
            ):
                self._latest_membership_snapshot = {
                    **self._latest_membership_snapshot,
                    "persistence_state": "persisted",
                }
            logger.info(
                "NAS TOP20 최신 순위 저장 완료: 회차=%s 종목=%d 조회후_ms=%d 저장_ms=%d",
                snapshot_key, len(codes),
                round((persistence_started_at - refresh_started_at) * 1000),
                round((time.monotonic() - persistence_started_at) * 1000),
            )
            if enrichment_codes:
                if self._entrants_day != day:
                    first_seen, persisted = await self._load_daily_entrant_state(day)
                    self._entrant_first_seen = first_seen
                    self._entrant_persisted_codes = persisted
                    self._entrants_day = day
                for code in enrichment_codes:
                    self._entrant_first_seen.setdefault(code, snapshot_key)
                await self._persist_missing_daily_entrants(
                    day, self._entrant_first_seen, self._entrant_persisted_codes,
                )
            if update.completed is not None:
                try:
                    await self._flush_pending_indexes()
                except Exception as error:
                    logger.warning("NAS TOP20 지수 저장 실패(재시도 예정): %s", error)
            if self._tasks and ranking_history_due(self._now()):
                self._schedule_fundamentals(enrichment_codes, day)
                self._schedule_aux_rankings(now)
            return codes

    async def _load_daily_entrant_state(
        self, day: str, documents: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, str], set[str]]:
        """Recover a day locally, including membership commits whose document save failed."""
        if documents is None:
            documents = await owned_to_thread(
                self._store.load_documents, "top20_daily_entrants", day, 5000,
            )
        snapshots = await owned_to_thread(
            self._store.load_dataset_snapshots, "top20_membership", day, 5000,
        )
        revisions = await owned_to_thread(
            self._store.load_observation_revisions, "top20_membership", day, 5000,
        )
        if any(len(rows) >= 5000 for rows in (documents, snapshots, revisions)):
            raise RuntimeError(f"TOP20 daily entrant recovery limit reached: {day}")
        persisted = {str(row["key"]).strip() for row in documents if row.get("key")}
        first_seen = {
            str(row["key"]).strip(): str(row.get("document", {}).get("first_seen_at", ""))
            for row in documents if row.get("key")
        }
        for row in [*snapshots, *revisions]:
            payload = row.get("payload", {})
            observed_at = str(payload.get("observed_at") or row.get("snapshot_key")
                              or row.get("effective_at") or "")
            if not observed_at:
                continue
            observed = datetime.fromisoformat(observed_at)
            observed = observed.replace(tzinfo=KST) if observed.tzinfo is None else observed
            for value in payload.get("codes", []):
                # Stored membership codes are already normalized; preserve their identities.
                code = str(value).strip()
                if not code or code in persisted:
                    continue
                previous = first_seen.get(code)
                if previous:
                    previous_at = datetime.fromisoformat(previous)
                    previous_at = previous_at.replace(tzinfo=KST) if previous_at.tzinfo is None else previous_at
                    if previous_at <= observed:
                        continue
                first_seen[code] = observed_at
        return first_seen, persisted

    async def _persist_missing_daily_entrants(
        self, day: str, first_seen: dict[str, str], persisted: set[str],
    ) -> None:
        documents = [
            {"owner": day, "key": code, "document": {"code": code, "first_seen_at": seen}}
            for code, seen in first_seen.items() if code not in persisted and self.stock_enrichment_codes((code,), day=day)
        ]
        if not documents:
            return
        await owned_to_thread(self._store.upsert_documents, "top20_daily_entrants", documents)
        # Failed or cancelled saves remain pending, even if the code leaves the next ranking.
        persisted.update(row["key"] for row in documents)

    def _schedule_market_catalog(self, day: str) -> None:
        task = self._market_catalog_task
        if task is not None and not task.done():
            return
        task = owned_create_task(
            self._ensure_market_catalog(day), name="nas-market-catalog",
        )
        self._market_catalog_task = task
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._market_catalog_done)

    def _market_catalog_done(self, task: asyncio.Task[None]) -> None:
        self._fundamentals_tasks.discard(task)
        if self._market_catalog_task is task:
            self._market_catalog_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("NAS KRX 종목 카탈로그 준비 실패(순위 수집 계속): %s", error)

    def _schedule_nxt_catalog(self, now: datetime) -> None:
        day = as_kst(now).date().isoformat()
        task = self._nxt_catalog_task
        if (not nxt_catalog_refresh_due(now) or self._premarket_catalog_day == day
                or (task is not None and not task.done()) or time.monotonic() < self._nxt_catalog_retry_at):
            return
        task = owned_create_task(self._prepare_nxt_catalog(day), name="top20-nxt-catalog")
        self._nxt_catalog_task = task
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._fundamentals_tasks.discard)

    async def _prepare_nxt_catalog(self, day: str) -> None:
        try:
            await self._load_nxt_catalog(day)
            if self._premarket_catalog_day == day:
                return
            adapter = _AsyncBrokerChartAdapter(self._broker, asyncio.get_running_loop())

            def request_page(*args, **kwargs):
                now = as_kst(self._now())
                if now.date().isoformat() != day or not nxt_catalog_refresh_due(now):
                    raise ValueError("NXT 종목 목록 수집 시간이 종료됐습니다.")
                return adapter.request_with_continuation(*args, **kwargs)

            catalog = await owned_to_thread(load_premarket_catalog, request_page)
            if as_kst(self._now()).date().isoformat() != day:
                return
            async with self._nxt_catalog_lock:
                await owned_to_thread(self._store.save_dataset_snapshot, NXT_CATALOG_KIND, day, day, catalog)
                self._nxt_catalogs[day] = catalog["flags"]
                self._install_premarket_markets(day, catalog["stocks"])
            if ranking_history_due(self._now()):
                self._publish_known_subscription()
        except Exception as error:
            self._nxt_catalog_retry_at = time.monotonic() + 300
            logger.warning("NXT 종목 목록 준비 실패: %s", error)

    async def _load_nxt_catalog(self, day: str) -> dict[str, bool] | None:
        async with self._nxt_catalog_lock:
            if day not in self._nxt_catalogs:
                values = await owned_to_thread(self._store.load_dataset_snapshots, NXT_CATALOG_KIND, day, 1)
                payload = values[0].get("payload", {}) if values else {}
                flags = payload.get("flags")
                stocks = payload.get("stocks")
                if isinstance(stocks, dict) and stocks and isinstance(flags, dict):
                    self._install_premarket_markets(day, stocks)
                self._nxt_catalogs[day] = flags if isinstance(flags, dict) else None
                if len(self._nxt_catalogs) > 2:
                    del self._nxt_catalogs[min(self._nxt_catalogs)]
                return flags if isinstance(flags, dict) else None
            return self._nxt_catalogs[day]

    def _install_premarket_markets(self, day: str, stocks: dict[str, tuple[str, str]]) -> None:
        if day >= self._premarket_catalog_day:
            self._premarket_markets = {code: market for code, (_, market) in stocks.items()}
            self._premarket_catalog_day = day

    def _schedule_subscription_update(self, day: str) -> None:
        self._subscription_revision += 1
        self._subscription_day = day
        # TOP20의 KRX 구독은 NXT 여부나 계좌 편입 종목 조회를 기다리지 않는다.
        self._publish_known_subscription()
        task = self._subscription_task
        if task is not None and not task.done():
            return
        task = owned_create_task(
            self._refresh_subscription_until_current(), name="nas-top20-subscription",
        )
        self._subscription_task = task
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._subscription_done)

    def _subscription_done(self, task: asyncio.Task[None]) -> None:
        self._fundamentals_tasks.discard(task)
        if self._subscription_task is task:
            self._subscription_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("NAS TOP20 구독 보완 실패(순위 수집 계속): %s", error)

    async def _refresh_subscription_until_current(self) -> None:
        while ranking_history_due(self._now()):
            revision = self._subscription_revision
            day = self._subscription_day
            account_entry_codes = await self._load_account_entry_codes(day)
            if not ranking_history_due(self._now()):
                return
            await self._update_subscription()
            if self._tasks and account_entry_codes and ranking_history_due(self._now()):
                self._schedule_fundamentals(account_entry_codes, day)
            if revision == self._subscription_revision:
                return

    def _schedule_aux_rankings(self, now: datetime) -> None:
        if not ranking_snapshot_archive_due(now):
            return
        due = []
        schedules = {
            "4": now.strftime("%Y-%m-%dT%H:%M:%S") if now.second in {0, 30} else "",
            "1": now.strftime("%Y-%m-%dT%H:%M") if now.second == 0 else "",
            "2": now.strftime("%Y-%m-%dT%H:%M") if now.second == 0 and now.minute % 10 == 0 else "",
            "3": now.strftime("%Y-%m-%dT%H") if now.second == 0 and now.minute == 0 else "",
        }
        for query_type, slot in schedules.items():
            if slot and self._last_aux_ranking_slots.get(query_type) != slot:
                self._last_aux_ranking_slots[query_type] = slot
                due.append(query_type)
        if not due:
            return
        task = owned_create_task(
            self._refresh_aux_rankings(tuple(due)), name="nas-aux-rankings",
        )
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._fundamentals_tasks.discard)

    async def _refresh_aux_rankings(self, query_types: tuple[str, ...]) -> None:
        for query_type in query_types:
            try:
                await self._broker.request(
                    "ka00198", "/api/dostk/stkinfo", {"qry_tp": query_type},
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("NAS 순위 기준 %s 갱신 실패: %s", query_type, error)

    async def _ensure_market_catalog(self, day: str) -> None:
        if self._stock_master_required:
            await self._ensure_stock_master(day)
        now = self._now()
        if self._listing_evidence is not None and self._listing_evidence.refresh_due(now):
            await await_owned_task(owned_create_task(owned_to_thread(
                self._listing_evidence.refresh, now,
            ), name="kind-listing-evidence"))
        if self._market_catalog_day == day and (self._markets or self._premarket_catalog_day == day):
            return
        if self._premarket_catalog_day != day:
            await self._load_nxt_catalog(day)
        stored = await owned_to_thread(
            self._store.load_documents, "stock_catalog", "krx", 10_000,
        )
        meta_day = ""
        markets: dict[str, str] = {}
        for value in stored:
            document = value.get("document", {})
            if not isinstance(document, dict):
                continue
            if value.get("key") == "_meta":
                meta_day = str(document.get("as_of", ""))
                continue
            code = str(document.get("code", value.get("key", ""))).strip()
            market = str(document.get("market", "")).strip()
            if code and market:
                markets[code] = market
        if markets:
            self._markets = markets
        if self._premarket_catalog_day == day or (meta_day == day and markets):
            self._market_catalog_day = day
            return
        # A late startup can now collect the Kiwoom list during ranking hours.
        # The ranking loop can retry KIND fallback after the Kiwoom task finishes.
        if self._nxt_catalog_task is not None and not self._nxt_catalog_task.done():
            return
        observer = self._catalog_input_observer
        loader = observer.captured_catalog_loader(self) if observer is not None else self._catalog_loader
        try:
            rows = await owned_to_thread(loader)
        except Exception as error:
            # 이미 보존된 카탈로그가 있으면 순위 수집은 계속한다. 시장이 없는
            # 종목만 기존처럼 '시장 미확인'으로 남겨 잘못 확정하지 않는다.
            logger.warning("NAS KRX 종목 카탈로그 갱신 실패(기존 자료 사용): %s", error)
            self._market_catalog_day = day if markets else ""
            return
        unique_rows: dict[str, tuple[str, str]] = {}
        for code, name, market in rows:
            if code:
                unique_rows.setdefault(code, (name, market))
        documents = [
            {"owner": "krx", "key": code, "document": {
                "code": code, "name": name, "market": market, "as_of": day,
            }}
            for code, (name, market) in unique_rows.items()
        ]
        documents.append({
            "owner": "krx", "key": "_meta",
            "document": {"as_of": day, "rows": len(unique_rows)},
        })
        with observer.catalog_recording_scope(loader) if observer is not None else nullcontext():
            await owned_to_thread(self._store.replace_documents, "stock_catalog", documents)
        self._markets = {
            code: market for code, (_name, market) in unique_rows.items() if market
        }
        self._market_catalog_day = day



    async def _schedule_loop(self) -> None:
        with (self._capture_scope(self, "top20", "autonomous_top20")
              if self._capture_scope is not None else nullcontext()):
            first_iteration = True
            while True:
                now = self._now()
                self._schedule_nxt_catalog(now)
                slot = _ranking_expected_at(now).strftime("%Y-%m-%dT%H:%M:%S")
                if first_iteration or slot != self._last_ranking_slot:
                    self._last_ranking_slot = slot
                    try:
                        await self.refresh_ranking_once(now)
                    except Exception as error:
                        logger.warning("recording_gap: NAS TOP20 순위 조회/저장 실패: %s", error)
                first_iteration = False
                await self.after_close.consider(now)
                await asyncio.sleep(0.25)

    async def _is_observed_krx_trading_day(self, day: str) -> bool:
        cached = self._krx_trading_day_cache.get(day)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]
        try:
            documents = await owned_to_thread(
                self._store.load_documents, "krx_trading_day_observations", day, 1,
            )
        except Exception as error:
            logger.warning("KRX 거래일 증거 조회 실패(%s): %s", day, error)
            return False
        confirmed = any(
            isinstance(value, dict)
            and isinstance(value.get("document"), dict)
            and str(value["document"].get("trading_date", "")) == day
            for value in documents
        )
        self._krx_trading_day_cache[day] = (time.monotonic() + 60.0, confirmed)
        return confirmed

    async def _previous_observed_krx_trading_day(self, value: datetime) -> str:
        candidate = value.date() - timedelta(days=1)
        for _ in range(14):
            if candidate.weekday() < 5 and await self._is_observed_krx_trading_day(candidate.isoformat()):
                return candidate.isoformat()
            candidate -= timedelta(days=1)
        return ""

    def _save_market_operation_day(self, day: str, tick: MarketOperationTick) -> None:
        observed_at = self._now().isoformat(timespec="seconds")
        self._store.upsert_documents("krx_trading_day_observations", [{
            "owner": day,
            "key": "latest",
            "document": {
                "trading_date": day,
                "source": "kiwoom_websocket_0s",
                "status_code": tick.status_code,
                "trade_time": tick.trade_time or "",
                "remaining_time": tick.remaining_time or "",
                "observed_at": observed_at,
            },
        }])

    def _market_operation_observation_saved(self, task: asyncio.Task[Any]) -> None:
        self._fundamentals_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("키움 0s 거래일 증거 저장 실패: %s", error)



    async def _event_loop(self) -> None:
        with (self._capture_scope(self, "top20", "autonomous_top20")
              if self._capture_scope is not None else nullcontext()):
            while True:
                subscriber = self._subscriber
                if subscriber is None:
                    await asyncio.sleep(0.1)
                    continue
                event = await subscriber.queue.get()
                observer = self._ranking_input_observer
                with observer.consumed_delivery(subscriber, event) if observer is not None else nullcontext():
                    if not isinstance(event.get("payload"), dict):
                        continue
                    payload = event["payload"]
                    if event.get("type") == "market_operation":
                        allowed = {name for name in MarketOperationTick.__dataclass_fields__}
                        try:
                            tick = MarketOperationTick(**{
                                key: value for key, value in payload.items() if key in allowed
                            })
                        except TypeError:
                            continue
                        # Only KRX phase notifications confirm a KRX trading date.
                        # No frame or stale state is treated as a holiday/calendar row.
                        if tick.status_code in {"0", "2", "3", "4", "8", "9", "a", "b", "c", "d"}:
                            day = self._now().date().isoformat()
                            self._krx_trading_day_cache[day] = (time.monotonic() + 60.0, True)
                            task = owned_create_task(
                                owned_to_thread(self._save_market_operation_day, day, tick),
                                name=f"krx-trading-day-observation-{day}",
                            )
                            self._fundamentals_tasks.add(task)
                            task.add_done_callback(self._market_operation_observation_saved)
                        continue
                    if event.get("type") == "program_trade":
                        allowed = {name for name in ProgramTradeTick.__dataclass_fields__}
                        try:
                            tick = ProgramTradeTick(**{
                                key: value for key, value in payload.items() if key in allowed
                            })
                        except TypeError:
                            continue
                        if not self.stock_enrichment_codes((tick.code,)):
                            continue
                        observed_at = self._now()
                        self._program_snapshots.enqueue({
                            "subject": tick.code,
                            "snapshot_key": (
                                f"{observed_at:%Y%m%d}:REALTIME:"
                                f"{(tick.trade_time or observed_at.strftime('%H%M%S')).replace(':', '')}"
                            ),
                            "payload": {"market": tick.market, "rows": [{
                                "available": True, "source": "kiwoom_realtime_0w",
                                "trade_time": tick.trade_time or "", "market": tick.market,
                                "net_buy_quantity": tick.net_buy_quantity,
                                "net_buy_quantity_change": tick.net_buy_quantity_change,
                                "net_buy_amount_million_won": tick.net_buy_amount_million_won,
                                "net_buy_amount_change_million_won": tick.net_buy_amount_change_million_won,
                            }]},
                        })
                        continue
                    if event.get("type") != "trade":
                        continue
                    allowed = {name for name in TradeTick.__dataclass_fields__}
                    try:
                        tick = TradeTick(**{key: value for key, value in payload.items() if key in allowed})
                    except TypeError:
                        continue
                    self._trade_values.ingest(tick, self._now())

    async def _index_loop(self) -> None:
        with (self._capture_scope(self, "top20", "autonomous_top20")
              if self._capture_scope is not None else nullcontext()):
            while True:
                try:
                    now = self._now()
                    async with self._collector_lock:
                        update = self._advance(now)
                        subscriber = self._subscriber
                        if subscriber is not None and subscriber.codes and not ranking_history_due(now):
                            self._hub.update_subscription(subscriber, [], [], program_codes=[], priority_codes=[])
                    # Register the immutable result durably; native save latency belongs
                    # to the separate persistence driver, not the sampling clock.
                    if update.completed is not None:
                        await self._queue_index(update.completed)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning("NAS TOP20 지수 수집 실패(계속 실행): %s", error)
                await asyncio.sleep(0.25)

    async def _persistence_loop(self) -> None:
        with (self._capture_scope(self, "top20", "autonomous_top20")
              if self._capture_scope is not None else nullcontext()):
            while True:
                try:
                    await self._flush_pending_indexes()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning("NAS TOP20 지수 저장 실패(재시도 예정): %s", error)
                try:
                    await self._program_snapshots.flush()
                    await self._persist_program_gaps()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning("NAS TOP20 프로그램수급 저장 실패(재시도 예정): %s", error)
                await asyncio.sleep(0.25)

    def _advance(self, now: datetime):
        subscriber = self._subscriber
        if subscriber is not None:
            self._observe_program_connection(now, subscriber)
            if subscriber.dropped_events != self._observed_dropped_events:
                lost = subscriber.dropped_events - self._observed_dropped_events
                self._observed_dropped_events = subscriber.dropped_events
                self._collector.mark_gap()
                for gap in subscriber.take_gap(lost).ranges:
                    if gap.event_type == "program_trade":
                        self._remember_program_gap(gap.code, gap.start, gap.end)
                logger.warning("recording_gap: TOP20 실시간 소비자 큐에서 %d건 누락", lost)
        return self._collector.advance(
            now, enabled=True,
            # 순위 요청 성공만으로는 1분 거래대금이 완전하지 않다. NAS와
            # 키움 WebSocket 사이가 끊긴 동안에는 행을 만들지 않아 차트와
            # 과거 분석에서 실제 수집 공백으로 드러나게 한다.
            collection_open=_collection_open(now) and self._hub.upstream_ready,
            value_provider=lambda code, minute: self._trade_values.bucket_trade_value_eok(code, 1, minute),
            market_provider=lambda code: self._premarket_markets.get(code) or self._markets.get(code, ""),
        )

    async def _save_index(self, record: Top20MinuteRecord) -> None:
        await self._queue_index(record)
        await self._flush_pending_indexes()

    async def _queue_index(self, record: Top20MinuteRecord) -> None:
        payload = asdict(record)
        payload["minute"] = record.minute.isoformat(timespec="minutes")
        payload["total"] = record.total
        snapshot_key = str(payload["minute"])
        async with self._index_outbox_lock:
            self._pending_index_records[snapshot_key] = payload
            if self._outbox is not None:
                await owned_to_thread(self._outbox.put, snapshot_key, payload)

    async def _flush_pending_indexes(self) -> None:
        # Each caller waits for its entry keys, without draining later arrivals forever.
        keys = tuple(self._pending_index_records)
        async with self._index_flush_lock:
            task = self._index_save_task
            if task is not None and not task.done():
                # A cancelled waiter can release the lock while native I/O still owns it.
                await asyncio.shield(task)
            # The independent driver can see pending RAM while its producer is
            # still registering the durable outbox. Detach only after that handoff.
            async with self._index_outbox_lock:
                batch = tuple(
                    (key, self._pending_index_records[key])
                    for key in keys if key in self._pending_index_records
                )
            if not batch:
                return
            task = owned_create_task(self._write_pending_indexes(batch), name="nas-top20-index-save")
            self._index_save_task = task
            task.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None,
            )
            # The lock serializes caller cuts; the task owns uncancellable native I/O.
            await asyncio.shield(task)

    async def _write_pending_indexes(
        self, batch: tuple[tuple[str, dict[str, Any]], ...],
    ) -> None:
        for snapshot_key, payload in batch:
            await self._write_index(payload)
            async with self._index_outbox_lock:
                if self._pending_index_records.get(snapshot_key) is payload:
                    if self._outbox is not None:
                        await owned_to_thread(self._outbox.remove, snapshot_key)
                    self._pending_index_records.pop(snapshot_key, None)

    async def _write_index(self, payload: dict[str, Any]) -> None:
        snapshot_key = str(payload["minute"])
        subject = snapshot_key[:10]
        await owned_to_thread(
            self._store.save_dataset_snapshot,
            "top20_index",
            subject,
            snapshot_key,
            payload,
            observation=top20_index_observation(
                subject,
                snapshot_key,
                payload,
                self._now(),
                str(payload.get("capture_state", "partial")),
            ),
        )

    def _publish_known_subscription(self) -> None:
        if self._subscriber is None:
            return
        top20_codes = self._collector.realtime_codes((), enabled=True)
        realtime_codes = tuple(dict.fromkeys((
            *top20_codes,
            *self._account_entry_codes,
        )))
        today = as_kst(self._now()).date().isoformat()
        flags = self._nxt_catalogs.get(today) or {}
        nxt_codes = [code for code in realtime_codes if flags.get(code) is True]
        self._hub.update_subscription(
            self._subscriber, list(realtime_codes), nxt_codes,
            program_codes=list(realtime_codes),
            priority_codes=list(realtime_codes),
        )

    async def _update_subscription(self) -> None:
        if self._subscriber is None:
            return
        await self._load_nxt_catalog(as_kst(self._now()).date().isoformat())
        if ranking_history_due(self._now()):
            self._publish_known_subscription()

    async def _load_account_entry_codes(self, day: str) -> tuple[str, ...]:
        """실제 매수 체결 종목을 순위 계산과 분리된 시장자료 수집 대상으로 읽는다."""
        values = await owned_to_thread(
            self._store.load_documents, "account_entry_symbols_daily", day, 5000,
        )
        codes = tuple(dict.fromkeys(filter(None, (
            normalize_stock_code(value.get("key")) for value in values
        ))))
        self._account_entry_codes = self.stock_enrichment_codes(codes)
        return self._account_entry_codes

    async def _nxt_enabled(self, code: str, day: str = "") -> bool | None:
        flags = await self._load_nxt_catalog(day or as_kst(self._now()).date().isoformat())
        value = flags.get(code) if flags is not None else None
        return value if isinstance(value, bool) else None

    def _schedule_fundamentals(self, codes: tuple[str, ...], day: str) -> None:
        minutes_due = (self._minute_backfill_enabled and not self._workload_paused("minute_backfill")
                       and _entry_minute_query_due(day, self._now()))
        missing = tuple(
            code for code in codes
            if code not in self._fundamentals_pending
            and (not self._entry_data_complete(code, day)
                 or (minutes_due and self._entry_stage_ready.get((code, "minutes")) != day))
        )
        if not missing:
            return
        self._fundamentals_pending.update(missing)
        task = owned_create_task(
            self._ensure_fundamentals(missing, day),
            name="nas-top20-entry-data",
        )
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._fundamentals_tasks.discard)

    async def _ensure_fundamentals(self, codes: tuple[str, ...], day: str = "") -> None:
        """Retry failed stages without repeating successful stock/day preparation."""
        with (self._capture_scope(self, "top20", "autonomous_top20")
              if self._capture_scope is not None else nullcontext()):
            current = self._now()
            current_day = (
                current.date() if current.tzinfo is None
                else current.astimezone(KST).date()
            )
            target_day = day or current_day.isoformat()
            try:
                for code in codes:
                    self._entry_data_ready.pop(code, None)
                    self._fundamentals_ready.pop(code, None)
                    for stage, prepare in (
                        ("basic", self._ensure_entry_basic),
                        ("minutes", self._backfill_entry_minutes),
                        ("daily", self._ensure_entry_daily_history),
                        ("flow", self._capture_candidate_investor_flow),
                        ("high", self._ensure_historical_high),
                    ):
                        if stage != "basic" and not day:
                            continue
                        token = self._entry_stage_token(stage, target_day)
                        if self._entry_stage_is_ready(code, stage, target_day):
                            continue
                        if stage == "minutes" and (
                            not self._minute_backfill_enabled or self._workload_paused("minute_backfill")
                            or not _entry_minute_query_due(target_day, self._now())
                        ):
                            continue
                        if stage == "flow" and not _candidate_investor_flow_due(target_day, self._now()):
                            continue
                        if stage == "high" and not self._entry_stage_is_ready(code, "daily", target_day):
                            # Historical-high calculation depends on verified daily history.
                            continue
                        try:
                            input_versions = self.daily_reference.source_versions(code) if stage == "high" else None
                            if stage == "minutes":
                                from .postgres_access import db_call_source
                                with db_call_source("top20.entry_minutes"):
                                    if not await prepare(code, target_day):
                                        continue
                            else:
                                result = await prepare(code, target_day)
                                if stage in {"flow", "daily"} and result is False:
                                    continue
                            if stage == "daily":
                                if not self.daily_reference.stage_is_current(code, stage):
                                    raise RuntimeError("일봉 검증 중 입력이 변경되어 재검증이 필요합니다.")
                            elif stage == "high":
                                self.daily_reference.accept_high(code, input_versions)
                            self._entry_stage_ready[(code, stage)] = token
                        except asyncio.CancelledError:
                            raise
                        except Exception as error:
                            logger.warning("TOP20 편입종목 자료 준비 실패: %s stage=%s %s", code, stage, error)
                    if self._entry_data_complete(code, target_day, basics_only=not day):
                        self._entry_data_ready[code] = target_day
                        if (not day or not self._minute_backfill_enabled
                                or self._entry_stage_ready.get((code, "minutes")) == target_day):
                            self._fundamentals_ready[code] = target_day
            finally:
                self._fundamentals_pending.difference_update(codes)

    def _entry_stage_token(self, stage: str, day: str) -> str:
        return (fundamentals_refresh_key(date.fromisoformat(day), self._now())
                if stage == "basic" else day)

    def _entry_data_complete(self, code: str, day: str, *, basics_only: bool = False) -> bool:
        stages = ("basic",) if basics_only else ("basic", "daily", "flow", "high")
        if not _candidate_investor_flow_due(day, self._now()):
            stages = tuple(stage for stage in stages if stage != "flow")
        return all(self._entry_stage_is_ready(code, stage, day) for stage in stages)

    def _entry_stage_is_ready(self, code: str, stage: str, day: str) -> bool:
        if self._entry_stage_ready.get((code, stage)) != self._entry_stage_token(stage, day):
            return False
        if stage.startswith("daily:"):
            return self.daily_reference.market_is_current(code, stage.split(":", 1)[1])
        if stage in {"daily", "high"}:
            return self.daily_reference.stage_is_current(code, stage)
        return True

    async def _ensure_entry_basic(self, code: str, day: str) -> None:
        stored = await owned_to_thread(self._store.load_documents, "stock_fundamentals", code, 1)
        document = stored[0].get("document", {}) if stored else {}
        if not fundamentals_document_is_current(document, date.fromisoformat(day), checked_at=self._now()):
            result = await self._broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": code})
            if result.recording_succeeded is False or not result.payload:
                raise RuntimeError(f"{code} 기본정보 응답 또는 중앙 저장이 완료되지 않았습니다.")

    async def _ensure_entry_daily_history(self, code: str, day: str) -> bool:
        """Verify the source window, even when old canonical rows already exist."""
        # Each market retains success even when the other market fails.
        failure: Exception | None = None
        markets = ["KRX"]
        eligible = None
        try:
            if not self._entry_stage_is_ready(code, "daily:KRX", day):
                await self._ensure_daily_history(code, day, "KRX", scope="initial")
                self._entry_stage_ready[(code, "daily:KRX")] = day
        except Exception as error:
            failure = error
        try:
            eligible = await self._nxt_enabled(code, day)
            if eligible is True:
                markets.append("NXT")
                if not self._entry_stage_is_ready(code, "daily:NXT", day):
                    await self._ensure_daily_history(code, day, "NXT", scope="initial")
                    self._entry_stage_ready[(code, "daily:NXT")] = day
        except Exception as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure
        if eligible is None:
            return False
        self.daily_reference.accept_daily(code, markets)
        return True

    async def _ensure_daily_history(self, code: str, day: str, market: str, *, scope: str) -> dict[str, Any]:
        async with self.daily_reference.history_lease(code, market):
            return await self._collect_daily_history(code, day, market, scope=scope)

    async def _collect_daily_history(self, code: str, day: str, market: str, *, scope: str) -> dict[str, Any]:
        from .postgres_access import db_call_source
        current = self._now()
        current = current.astimezone(KST) if current.tzinfo else current.replace(tzinfo=KST)
        basis = current.date().isoformat()
        owner = f"{code}:{market}"
        source = "top20.after_close_daily" if scope == "final" else "top20.entry_daily_history"
        read_limit = 250 if day == basis else 5000
        version = self.daily_reference.source_versions(code)[market == "NXT"]
        # A historical target may have newer canonical rows preceding it.
        with db_call_source(source):
            stored = await owned_to_thread(self._store.load_daily_bars, code, market, read_limit)
            documents = await owned_to_thread(self._store.load_documents, DAILY_HISTORY_COLLECTION, owner, 2)
        for item in documents:
            evidence = item.get("document", {})
            if evidence.get("window_end") != day or (scope == "final" and evidence.get("scope") != "final"):
                continue
            coverage = assess_daily_coverage(stored, evidence, code=code, market=market, query_basis_date=basis)
            if coverage["collection_verified"]:
                self.daily_reference.accept_market(code, market, version)
                return coverage
        window = DailySourceWindow(basis, day)
        body = {"stk_cd": f"{code}_NX" if market == "NXT" else code,
                "base_dt": basis.replace("-", ""), "upd_stkpc_tp": "1"}
        cont_yn, next_key = "N", ""
        while not window.complete:
            if scope == "final":
                result = await self._request_with_retries("ka10081", "/api/dostk/chart", body, cont_yn, next_key)
            else:
                result = await self._broker.request("ka10081", "/api/dostk/chart", body,
                                                    cont_yn=cont_yn, next_key=next_key)
                if isinstance(self._broker, CentralRestBroker) and result.recording_succeeded is not True:
                    raise RuntimeError(f"{market} 일봉 응답 저장이 확인되지 않았습니다.")
            try:
                window.add_page(result.payload, result.has_next, result.next_key)
            except ValueError as error:
                raise RuntimeError(f"{market} 일봉 확보 미완료: {error}") from error
            cont_yn, next_key = "Y", result.next_key
        if scope == "final":
            close_at = full_day_close_at(date.fromisoformat(day), venue=market)
            current = self._now()
            current = current.astimezone(KST) if current.tzinfo else current.replace(tzinfo=KST)
            if (close_at is None or current < close_at
                    or not await self._is_observed_krx_trading_day(day)
                    or not any(row["trading_date"] == day for row in window.rows)):
                raise RuntimeError(f"{market} 일봉 장후 확정 근거가 없습니다.")
        evidence = window.evidence(code=code, market=market, scope=scope, checked_at=self._now().isoformat())
        version = self.daily_reference.source_versions(code)[market == "NXT"]
        with db_call_source(source):
            stored = await owned_to_thread(self._store.load_daily_bars, code, market, read_limit)
        coverage = assess_daily_coverage(stored, evidence, code=code, market=market, query_basis_date=basis)
        if not coverage["collection_verified"]:
            raise RuntimeError(f"{market} 일봉 응답과 저장값이 일치하지 않습니다.")
        await owned_to_thread(self._store.upsert_documents, DAILY_HISTORY_COLLECTION,
                                [{"owner": owner, "key": scope, "document": evidence}])
        self.daily_reference.accept_market(code, market, version)
        return coverage

    async def _backfill_entry_minutes(self, code: str, day: str) -> bool:
        """새 편입 종목의 등장 전 당일 분봉을 NAS가 한 번 준비한다."""
        if (not self._minute_backfill_enabled or self._workload_paused("minute_backfill")
                or not _entry_minute_query_due(day, self._now())):
            return False
        markets = ["KRX"]
        eligible = await self._nxt_enabled(code, day)
        if eligible is True:
            markets.append("NXT")
        for market in markets:
            owner = f"{day}:{code}:{market}"
            stored = await owned_to_thread(
                self._store.load_documents, "market_data_coverage_intraday", owner, 1,
            )
            if stored:
                continue
            body = {
                "stk_cd": f"{code}_NX" if market == "NXT" else code,
                "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": day.replace("-", ""),
            }
            cont_yn, next_key, pages = "N", "", 0
            expected_minutes: set[str] = set()
            while pages < 8:
                if self._workload_paused("minute_backfill"):
                    raise RuntimeError("diagnostic minute backfill pause")
                result = await self._request_with_retries(
                    "ka10080", "/api/dostk/chart", body, cont_yn, next_key,
                )
                pages += 1
                rows = result.payload.get("stk_min_pole_chart_qry", [])
                expected_minutes.update(_response_day_minutes(rows, day))
                dates = {
                    _raw_bar_date(row, day) for row in rows if isinstance(row, dict)
                } if isinstance(rows, list) else set()
                if (
                    not result.has_next or not result.next_key
                    or any(value < day.replace("-", "") for value in dates if value)
                ):
                    break
                cont_yn, next_key = "Y", result.next_key
            else:
                raise RuntimeError("편입 전 분봉 종료점을 8페이지 안에 확인하지 못했습니다.")
            await self._verify_backfilled_minutes(code, day, market, expected_minutes)
            await owned_to_thread(self._store.upsert_documents, "market_data_coverage_intraday", [{
                "owner": owner, "key": "entry_backfill", "document": {
                    "kind": "minute", "scope": "through_entry", "pages": pages,
                    "as_of": day, "completed_at": self._now().isoformat(),
                },
            }])
        return eligible is not None

    async def _capture_candidate_investor_flow(self, code: str, day: str) -> bool:
        """프리·정규·애프터장 후보 최초 편입 때 외국인·기관 수급을 확보한다."""
        observer = self._flow_input_observer
        with observer.candidate_flow_scope(self, code, day) if observer is not None else nullcontext():
            if not _candidate_investor_flow_due(day, self._now()):
                return False
            owner = f"{day}:{code}"
            existing = await owned_to_thread(
                self._store.load_documents, "candidate_flow_capture", owner, 1,
            )
            if observer is not None:
                observer.flow_marker_state(bool(existing))
            if existing:
                return True
            if not _candidate_investor_flow_due(day, self._now()):
                return False
            raw_day = day.replace("-", "")
            body = {
                "stk_cd": f"{code}_AL", "strt_dt": raw_day, "end_dt": raw_day,
                "orgn_prsm_unp_tp": "1", "for_prsm_unp_tp": "1",
            }
            try:
                if observer is None:
                    result = await self._broker.request("ka10045", "/api/dostk/mrkcond", body)
                else:
                    result = await observer.candidate_flow_request(self._broker, body, 0)
            except Exception:
                body["stk_cd"] = code
                if not _candidate_investor_flow_due(day, self._now()):
                    return False
                if observer is None:
                    result = await self._broker.request("ka10045", "/api/dostk/mrkcond", body)
                else:
                    result = await observer.candidate_flow_request(self._broker, body, 1)
            self._verify_flow_result(result, "ka10045", "stk_orgn_trde_trnsn", raw_day)
            marker_at = self._now()
            if observer is not None:
                observer.flow_marker_time(marker_at)
            await owned_to_thread(self._store.upsert_documents, "candidate_flow_capture", [{
                "owner": owner, "key": "initial", "document": {
                    "as_of": day, "captured_at": marker_at.isoformat(),
                    "scope": "candidate_first_seen",
                },
            }])
            return True

    async def _ensure_historical_high(self, code: str, day: str) -> None:
        """TOP20 후보의 수정주가 기준 역사적 고가를 NAS가 하루 한 번 계산한다."""
        input_versions = self.daily_reference.source_versions(code)
        stored = await owned_to_thread(
            self._store.load_documents, "historical_highs", code, 1,
        )
        loop = asyncio.get_running_loop()
        adapter = _AsyncBrokerChartAdapter(self._broker, loop)
        from .postgres_access import db_call_source
        with db_call_source("top20.historical_high"):
            bars = await owned_to_thread(self._store.load_daily_bars, code, "KRX", 250)
            coverage_docs = await owned_to_thread(self._store.load_documents, DAILY_HISTORY_COLLECTION, f"{code}:KRX", 2)
        current = self._now()
        basis = (current.astimezone(KST) if current.tzinfo else current).date().isoformat()
        coverage = choose_daily_coverage(bars, coverage_docs, code=code, market="KRX", query_basis_date=basis)
        verified_bars = [value for value in bars if str(value.get("trading_date", "")) <= coverage.get("window_end", "")]
        verified_bars = verified_bars[:coverage.get("expected_count", 0)]
        high_250 = (max(
            (int(value.get("high", 0)) for value in verified_bars if value.get("high") is not None),
            default=0,
        ) or None) if coverage["periods"]["250"]["status"] in {"ready", "provisional"} else None
        include_nxt = await self._nxt_enabled(code, day)
        daily_inputs = {"schema_version": 1, "markets": {
            "KRX": self._historical_daily_identity(bars, coverage),
        }}
        if include_nxt:
            with db_call_source("top20.historical_high"):
                nxt_bars = await owned_to_thread(self._store.load_daily_bars, code, "NXT", 250)
                nxt_docs = await owned_to_thread(self._store.load_documents, DAILY_HISTORY_COLLECTION, f"{code}:NXT", 2)
            nxt_coverage = choose_daily_coverage(nxt_bars, nxt_docs, code=code, market="NXT", query_basis_date=basis)
            daily_inputs["markets"]["NXT"] = self._historical_daily_identity(nxt_bars, nxt_coverage)
        if input_versions != self.daily_reference.source_versions(code):
            raise RuntimeError("신고가 입력 확인 중 일봉이 변경되었습니다.")
        document = stored[0].get("document", {}) if stored else {}
        if str(document.get("checked_on", "")) >= day and document.get("daily_inputs") == daily_inputs:
            return
        target = await owned_to_thread(
            HistoricalHighService(
                adapter, include_nxt=include_nxt,
                high_250_loader=lambda _code: high_250,
            ).load,
            code,
            as_of=date.fromisoformat(basis),
        )
        if input_versions != self.daily_reference.source_versions(code):
            raise RuntimeError("신고가 계산 중 일봉이 변경되어 재검증이 필요합니다.")
        await owned_to_thread(self._store.upsert_documents, "historical_highs", [{
            "owner": code, "key": "latest", "document": {
                "checked_on": day, "target": asdict(target), "daily_inputs": daily_inputs,
            },
        }])
        if input_versions != self.daily_reference.source_versions(code):
            raise RuntimeError("신고가 저장 중 일봉이 변경되어 재검증이 필요합니다.")

    @staticmethod
    def _historical_daily_identity(bars: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, Any]:
        # Timestamps alone cannot invalidate a calculation. Persist only actual
        # canonical inputs and the verified-window contract for restart recovery.
        return {"bars": fingerprint(bars), "coverage": {
            key: coverage.get(key) for key in (
                "collection_verified", "scope", "window_end", "query_basis_date", "expected_count", "periods",
            )
        }}

    def _observe_program_connection(self, now: datetime, subscriber: RealtimeSubscriber) -> None:
        now = as_kst(now)
        if not _collection_open(now):
            self._finish_program_outage(now)
            self._program_ready_codes.clear()
        elif self._hub.upstream_ready:
            self._finish_program_outage(now)
            if self._program_ready_codes != subscriber.program_codes:
                self._program_ready_codes = set(subscriber.program_codes)
        elif self._program_ready_codes:
            self._program_outage = (now, self._program_ready_codes)
            self._program_ready_codes = set()

    def _finish_program_outage(self, now: datetime) -> None:
        if self._program_outage is None:
            return
        start, codes = self._program_outage
        end = min(as_kst(now), start.replace(hour=20, minute=0, second=0, microsecond=0))
        for code in codes:
            self._remember_program_gap(code, start, end)
        self._program_outage = None

    def _remember_program_gap(self, code: str, start: datetime, end: datetime) -> None:
        start, end = as_kst(start), as_kst(end)
        if not code or not _collection_open(start) or not start <= end or start.date() != end.date():
            return
        first, last = start.strftime("%H%M%S"), end.strftime("%H%M%S")
        owner, key = f"{start.date().isoformat()}:{code}", f"gap:{first}:{last}"
        self._pending_program_gaps[(owner, key)] = {
            "owner": owner, "key": key, "document": {"start": first, "end": last},
        }

    async def _persist_program_gaps(self) -> None:
        if not self._pending_program_gaps:
            return
        async with self._program_gap_lock:
            pending = self._pending_program_gaps.copy()
            if not pending:
                return
            await await_owned_task(owned_create_task(owned_to_thread(
                self._store.upsert_documents, "candidate_flow_finalization", list(pending.values()),
            ), name="nas-program-gap-save"))
            for key in pending:
                self._pending_program_gaps.pop(key, None)

    async def _backfill_candidate_flows(self, code: str, day: str) -> None:
        """Only the current day's missing program-flow ranges need Kiwoom REST."""
        now = as_kst(self._now())
        if now.date().isoformat() != day or now.time() < clock_time(20, 5):
            return
        await self._program_snapshots.flush()
        await self._persist_program_gaps()
        owner = f"{day}:{code}"
        coverage = await owned_to_thread(
            self._store.load_documents, "candidate_flow_finalization", owner, 5000,
        )
        if any(row.get("key") == "complete" for row in coverage):
            return
        ranges = [(row["document"]["start"], row["document"]["end"])
                  for row in coverage if str(row.get("key", "")).startswith("gap:")]
        raw_day = day.replace("-", "")
        if not ranges:
            # The legacy SOR and recovery documents sort ahead of the latest REALTIME row.
            snapshots = await owned_to_thread(self._store.load_dataset_snapshots, "program_flow", code, 3)
            if any(str(value.get("snapshot_key", "")).startswith(f"{raw_day}:REALTIME:")
                   and any(row.get("net_buy_quantity") is not None or row.get("net_buy_amount_million_won") is not None
                           for row in value.get("payload", {}).get("rows", ())) for value in snapshots):
                return
            first_seen = self._entrant_first_seen.get(code, "")
            first = datetime.fromisoformat(first_seen).strftime("%H%M%S") if first_seen.startswith(day + "T") else "080000"
            ranges = [(first, "235959")]
        market, rows = await self._load_program_recovery(code, day, ranges)
        if as_kst(self._now()).date().isoformat() != day:
            raise RuntimeError("프로그램매매 보완 도중 조회 가능 거래일이 바뀌었습니다.")
        await owned_to_thread(self._store.save_dataset_snapshot, "program_flow", code,
                              f"{raw_day}:RECOVERY:{market}", {"market": market, "rows": rows})
        await owned_to_thread(self._store.upsert_documents, "candidate_flow_finalization", [{
            "owner": owner, "key": "complete", "document": {
                "as_of": day, "completed_at": self._now().isoformat(),
                "scope": "program_after_close", "ranges": ranges, "program_flow_rows": len(rows),
            },
        }])

    async def _load_program_recovery(self, code: str, day: str, ranges: list[tuple[str, str]]) -> tuple[str, list[dict[str, object]]]:
        raw_day = day.replace("-", "")
        request = (self._broker.request_unrecorded if isinstance(self._broker, CentralRestBroker)
                   else self._broker.request)
        body = {"amt_qty_tp": "1", "stk_cd": f"{code}_AL", "date": raw_day}
        market = "SOR"
        try:
            result = await request("ka90008", "/api/dostk/mrkcond", body)
        except Exception:
            if as_kst(self._now()).date().isoformat() != day:
                raise
            market = "KRX"
            body = {**body, "stk_cd": code}
            result = await request("ka90008", "/api/dostk/mrkcond", body)
        first_required = min(first for first, _ in ranges)
        seen_keys: set[str] = set()
        recovered: list[dict[str, object]] = []
        last_clock = "235959"
        while True:
            if as_kst(self._now()).date().isoformat() != day:
                raise RuntimeError("프로그램매매 보완 도중 조회 가능 거래일이 바뀌었습니다.")
            self._verify_flow_result(result, "ka90008", "stk_tm_prm_trde_trnsn", raw_day,
                                     allow_empty=not seen_keys and not result.has_next,
                                     require_recording=False)
            for row in program_trade_rows(result.payload["stk_tm_prm_trde_trnsn"]):
                clock = str(row["trade_time"])
                datetime.strptime(clock, "%H%M%S")
                if len(clock) != 6 or clock > last_clock:
                    raise RuntimeError("프로그램매매 연속조회 시각 순서가 올바르지 않습니다.")
                last_clock = clock
                if any(first <= clock <= last for first, last in ranges):
                    recovered.append(row)
            if not result.has_next or last_clock < first_required:
                return market, recovered
            key = result.next_key
            if not key or key in seen_keys:
                raise RuntimeError("프로그램매매 연속조회 키가 누락되거나 반복되었습니다.")
            seen_keys.add(key)
            result = await request("ka90008", "/api/dostk/mrkcond", body, cont_yn="Y", next_key=key)

    async def _backfill_market_indexes(self, day: str) -> None:
        """코스피·코스닥 분봉과 일봉을 NAS에서 거래일당 한 번 확정한다."""
        if await owned_to_thread(
            self._store.load_documents, "market_index_chart_coverage", day, 1,
        ):
            return
        raw_day = day.replace("-", "")
        completed: list[str] = []
        for market, code in (("kospi", "001"), ("kosdaq", "101")):
            minutes: list[dict[str, Any]] = []
            cont_yn, next_key = "N", ""
            terminated = False
            for _ in range(8):
                result = await self._broker.request(
                    "ka20005", "/api/dostk/chart",
                    {"inds_cd": code, "tic_scope": "1", "base_dt": raw_day},
                    cont_yn=cont_yn, next_key=next_key,
                )
                rows = result.payload.get("inds_min_pole_qry", [])
                if not isinstance(rows, list):
                    raise RuntimeError(f"{market} 지수 분봉 응답 형식이 올바르지 않습니다.")
                minutes.extend(value for value in rows if isinstance(value, dict))
                dates = {
                    str(value.get("cntr_tm", ""))[:8]
                    for value in rows if isinstance(value, dict)
                }
                if not result.has_next or any(value and value < raw_day for value in dates):
                    terminated = True
                    break
                if not result.next_key:
                    raise RuntimeError(f"{market} 지수 분봉 연속조회 키가 없습니다.")
                cont_yn, next_key = "Y", result.next_key
            if not terminated:
                raise RuntimeError(f"{market} 지수 분봉 8페이지 안에 종료점을 확인하지 못했습니다.")
            minutes = [row for row in minutes if str(row.get("cntr_tm", ""))[:8] == raw_day]
            if not minutes:
                raise RuntimeError(f"{market} {day} 지수 분봉이 없어 완료로 표시하지 않습니다.")
            daily_response = await self._broker.request(
                "ka20006", "/api/dostk/chart", {"inds_cd": code, "base_dt": raw_day},
            )
            daily_rows = [
                value for value in daily_response.payload.get("inds_dt_pole_qry", [])
                if isinstance(value, dict)
            ]
            await owned_to_thread(
                self._store.save_dataset_snapshot,
                "market_index_chart", f"{raw_day}:{market}", raw_day,
                {
                    "market": market, "as_of": day,
                    "minutes": minutes,
                    "daily": daily_rows,
                },
            )
            completed.append(market)
        await owned_to_thread(self._store.upsert_documents, "market_index_chart_coverage", [{
            "owner": day, "key": "complete", "document": {
                "as_of": day, "markets": completed,
                "completed_at": self._now().isoformat(),
            },
        }])

    async def _backfill_minutes(self, code: str, day: str, market: str) -> None:
        if not self._minute_backfill_enabled or self._workload_paused("minute_backfill"):
            return
        owner = f"{day}:{code}:{market}"
        coverage = await owned_to_thread(
            self._store.load_documents, "market_data_coverage", owner, 1,
        )
        if coverage:
            document = coverage[0].get("document", {})
            if (isinstance(document, dict) and market == "KRX" and document.get("kind") == "minute_no_trade"
                    and document.get("as_of") == day and document.get("reason") == "krx_full_day_halt"
                    and document.get("session_finalized") is True and document.get("daily_revision_id")
                    and document.get("halt_proof")
                    and not await owned_to_thread(self._store.load_minute_bars, code, day, market)):
                return
            if (
                isinstance(document, dict)
                and document.get("kind") == "minute"
                and document.get("window_closed") is True
                and document.get("session_finalized") is True
                and await owned_to_thread(self._store.load_minute_bars, code, day, market)
            ):
                return
            # 구 빌드는 kind만 기록했다. 행이 있다는 이유로 건너뛰지 않고
            # 전체 연속조회를 다시 수행해 명시적인 완료 문서로 승격한다.
        body = {"stk_cd": f"{code}_NX" if market == "NXT" else code,
                "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": day.replace("-", "")}
        cont_yn, next_key, pages = "N", "", 0
        complete = False
        expected_minutes: set[str] = set()
        expected_bars: dict[str, dict[str, Any]] = {}
        continuations: set[str] = set()
        while pages < 8:
            if self._workload_paused("minute_backfill"):
                raise RuntimeError("diagnostic minute backfill pause")
            result = await self._request_with_retries("ka10080", "/api/dostk/chart", body, cont_yn, next_key)
            pages += 1
            rows = result.payload.get("stk_min_pole_chart_qry", [])
            if not isinstance(rows, list) or result.recording_succeeded is False or (
                isinstance(self._broker, CentralRestBroker) and result.recording_succeeded is not True
            ):
                raise RuntimeError("분봉 응답/원본 적재가 확인되지 않았습니다.")
            parsed = parse_minute_response(body, result.payload, self._now())
            for bar in parsed:
                if bar["trading_date"] != day:
                    continue
                key = bar["minute"]
                prior = expected_bars.get(key)
                if prior is not None and any(prior[name] != bar[name] for name in BAR_FIELDS):
                    raise RuntimeError("분봉 연속조회 중 동일 분의 값이 서로 다릅니다.")
                expected_bars[key] = bar
            expected_minutes.update(_response_day_minutes(rows, day))
            dates = {_raw_bar_date(row, day) for row in rows if isinstance(row, dict)} if isinstance(rows, list) else set()
            if result.has_next and (not result.next_key or result.next_key in continuations):
                raise RuntimeError("분봉 continuation이 비었거나 반복됩니다.")
            if not result.has_next or any(value < day.replace("-", "") for value in dates if value):
                complete = True
                break
            cont_yn, next_key = "Y", result.next_key
            continuations.add(next_key)
        if not complete:
            raise RuntimeError("분봉 연속조회 8페이지 안에 대상일 종료점을 확인하지 못했습니다.")
        if not expected_minutes and market == "KRX" and await self._record_halt_without_minutes(code, day, pages):
            return
        await self._verify_backfilled_minutes(code, day, market, expected_minutes)
        receipt: dict[str, Any] = {}
        if set(expected_bars) == expected_minutes and expected_bars:
            stored = await owned_to_thread(self._store.load_minute_bars, code, day, market)
            selected = {row["minute"]: row for row in stored if row["minute"] in expected_minutes}
            if bar_fingerprint(selected) != bar_fingerprint(expected_bars):
                raise RuntimeError("분봉 응답과 저장 값이 다릅니다.")
            start = datetime.fromisoformat(day).replace(tzinfo=KST)
            observations = await owned_to_thread(self._store.load_market_data_metadata_range,
                MarketDatasetKind.MINUTE_BAR, f"{code}:{market}", start, start + timedelta(days=1))
            metadata = {item.observation_key[11:16]: market_metadata_document(item.metadata)
                        for item in observations if item.observation_key[11:16] in expected_minutes}
            if set(metadata) == expected_minutes and all(
                value["completeness"] == "complete" and value["origin"] in {"query", "backfilled"}
                and str(value["source"]).startswith("kiwoom-ka10080") for value in metadata.values()
            ):
                receipt = {"schema_version": "minute_backfill_coverage/v2", "request": body,
                           "expected_minutes": sorted(expected_minutes), "bars_fingerprint": bar_fingerprint(selected),
                           "metadata_fingerprint": metadata_fingerprint(metadata),
                           "termination": "past_day" if result.has_next else "end_of_response"}
        await owned_to_thread(self._store.upsert_documents, "market_data_coverage", [{
            "owner": owner, "key": "complete", "document": {
                "kind": "minute", "pages": pages, "scope": "full_day",
                "window_closed": True, "session_finalized": True,
                "as_of": day, "completed_at": self._now().isoformat(),
                **receipt,
            }
        }])
        return True

    async def _record_halt_without_minutes(self, code: str, day: str, pages: int) -> bool:
        if self._full_day_halt is None:
            return False
        daily = await owned_to_thread(self._store.load_documents, "market_data_coverage_daily", f"{code}:KRX", 1)
        coverage = daily[0]["document"] if daily else {}
        if (coverage.get("kind") != "daily" or coverage.get("as_of") != day or coverage.get("scope") != "full_day"
                or coverage.get("window_closed") is not True or coverage.get("session_finalized") is not True):
            return False
        revisions = await owned_to_thread(self._store.load_observation_revisions, "daily_bar", f"{code}:KRX", 500)
        exact = next((row for row in revisions if row["observation_key"] == day), None)
        if (exact is None or exact["completeness"] != "complete" or exact["source_id"] != "kiwoom-ka10081"
                or type(exact["payload"].get("volume")) is not int or exact["payload"]["volume"] != 0):
            return False
        if await owned_to_thread(self._store.load_minute_bars, code, day, "KRX"):
            return False
        try:
            proof = await owned_to_thread(self._full_day_halt, code, date.fromisoformat(day))
        except (OSError, ValueError) as error:
            logger.warning("KRX 거래정지 근거 조회 실패: %s %s (%s)", code, day, type(error).__name__)
            return False
        if proof is None:
            return False
        await owned_to_thread(self._store.upsert_documents, "market_data_coverage", [{
            "owner": f"{day}:{code}:KRX", "key": "complete", "document": {
                "kind": "minute_no_trade", "reason": "krx_full_day_halt", "as_of": day,
                "scope": "full_day", "window_closed": True, "session_finalized": True,
                "pages": pages, "daily_revision_id": exact["revision_id"], "daily_volume": 0,
                "halt_proof": proof, "completed_at": self._now().isoformat(),
            },
        }])
        return True

    def _verify_flow_result(
        self, result: Any, api_id: str, rows_key: str, raw_day: str,
        *, allow_empty: bool = False, require_recording: bool = True,
    ) -> None:
        if result.recording_succeeded is False or (
            require_recording and isinstance(self._broker, CentralRestBroker) and result.recording_succeeded is not True
        ):
            raise RuntimeError(f"{api_id} 수급 원본 저장이 확인되지 않았습니다.")
        rows = result.payload.get(rows_key)
        if not isinstance(rows, list) or (not rows and not allow_empty):
            raise RuntimeError(f"{api_id} 대상일 수급 응답이 비었습니다.")
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError(f"{api_id} 수급 응답 행 형식이 올바르지 않습니다.")
            row_day = str(row.get("dt", row.get("date", ""))).replace("-", "")
            if row_day and row_day != raw_day:
                raise RuntimeError(f"{api_id} 수급 응답에 대상일 외 자료가 있습니다.")

    async def _verify_backfilled_minutes(
        self, code: str, day: str, market: str, expected_minutes: set[str],
    ) -> None:
        if not expected_minutes:
            raise RuntimeError(f"{market} {day} 대상일 분봉이 없어 완료로 표시하지 않습니다.")
        stored = await owned_to_thread(self._store.load_minute_bars, code, day, market)
        stored_minutes = {str(row.get("minute", ""))[:5] for row in stored}
        if not expected_minutes.issubset(stored_minutes):
            raise RuntimeError(f"{market} {day} 응답 분봉 중 저장되지 않은 봉이 있습니다.")

    async def _backfill_daily(self, code: str, day: str, market: str) -> None:
        owner = f"{code}:{market}"
        coverage = await self._ensure_daily_history(code, day, market, scope="final")
        legacy = await owned_to_thread(self._store.load_documents, "market_data_coverage_daily", owner, 1)
        if legacy and legacy[0].get("document", {}).get("as_of") == day:
            return
        from .postgres_access import db_call_source
        with db_call_source("top20.after_close_daily"):
            bars = await owned_to_thread(self._store.load_daily_bars, code, market, 250)
        if not any(str(bar.get("trading_date", "")) == day for bar in bars):
            # NXT 가능 종목도 빈 응답을 완료로 기록하면 이후 모든 조회가
            # rows=0 캐시를 재사용해 KRX 단독 신고가로 내려간다. 어느
            # 시장이든 실제 일봉이 저장된 뒤에만 coverage를 확정한다.
            raise RuntimeError(f"일봉 응답은 성공했지만 저장된 {market} 일봉이 없습니다.")
        await owned_to_thread(self._store.upsert_documents, "market_data_coverage_daily", [{
            "owner": owner, "key": "complete", "document": {
                "kind": "daily", "scope": "full_day", "as_of": day,
                "window_closed": True, "session_finalized": True,
                "rows": coverage["expected_count"], "completed_at": self._now().isoformat(),
            }
        }])

    async def _request_with_retries(self, api_id: str, path: str, body: dict[str, Any], cont_yn: str, next_key: str):
        error: Exception | None = None
        for attempt in range(3):
            try:
                result = await self._broker.request(api_id, path, body, cont_yn=cont_yn, next_key=next_key)
                if isinstance(self._broker, CentralRestBroker) and api_id in {"ka10080", "ka10081"}:
                    if result.recording_succeeded is not True:
                        raise RuntimeError(f"{api_id} 조회는 성공했지만 봉 저장이 확인되지 않았습니다.")
                return result
            except Exception as current:
                error = current
                if attempt < 2:
                    await asyncio.sleep(1 + attempt * 2)
        assert error is not None
        raise error


def _collection_open(value: datetime) -> bool:
    current = value.time().replace(tzinfo=None)
    return value.weekday() < 5 and clock_time(8) <= current < clock_time(20)


def _entry_minute_query_due(day: str, value: datetime) -> bool:
    """Current-day entry bars cannot exist before the 08:00 market session."""
    current = value.astimezone(KST) if value.tzinfo else value
    return day != current.date().isoformat() or current.time() >= clock_time(8)


def _candidate_investor_flow_due(day: str, value: datetime) -> bool:
    """Request investor flow in the 08:00-20:00 pre/regular/after-market window."""
    current = value.astimezone(KST) if value.tzinfo else value
    return day == current.date().isoformat() and _collection_open(current)


def _ranking_collection_due(value: datetime) -> bool:
    """조회순위는 장 운영시간과 무관하게 매 30초 경계에 갱신한다."""
    return value.second in {0, 30}


def _ranking_expected_at(value: datetime) -> datetime:
    """현재 시각이 속한 30초 순위 회차의 기준 시각을 반환한다."""
    second = 30 if value.second >= 30 else 0
    return value.replace(second=second, microsecond=0, tzinfo=None)


def _ranking_response_has_empty_slots(items: list[dict[str, Any]]) -> bool:
    """20행 응답 안의 빈 순위 자리를 키움 갱신 중 부분 응답으로 판정한다."""
    if len(items) < 20:
        return True
    valid_count = sum(
        bool(_stock_code(row) and str(row.get("stk_nm", "")).strip())
        for row in items[:20]
    )
    return valid_count < 20


def _stock_code(row: dict[str, Any]) -> str:
    raw = str(row.get("stk_cd", row.get("code", ""))).strip()
    return normalize_stock_code(raw)


def _ranking_snapshot_key(items: list[dict[str, Any]], fallback: datetime, payload: dict[str, Any] | None = None) -> str:
    observed_at = _ranking_snapshot_at(items, payload)
    if observed_at is not None:
        return observed_at.isoformat(timespec="seconds")
    return fallback.isoformat(timespec="seconds")


def _ranking_snapshot_at(items: list[dict[str, Any]], payload: dict[str, Any] | None = None) -> datetime | None:
    first = items[0] if items else {}
    payload = payload or {}
    raw_date = str(first.get("dt", payload.get("base_date", ""))).strip()
    raw_time = str(first.get("tm", payload.get("base_time", ""))).strip().zfill(6)
    if len(raw_date) == 8 and raw_date.isdigit() and len(raw_time) == 6 and raw_time.isdigit():
        try:
            return datetime.strptime(f"{raw_date}{raw_time}", "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


def _stale_ranking_retry_delay(retry_index: int) -> float:
    index = max(0, retry_index)
    if index < 2:
        return 0.25
    if index < 4:
        return 0.5
    return 0.75


def _raw_bar_date(row: dict[str, Any], fallback_day: str) -> str:
    raw = str(row.get("cntr_tm", "")).strip()
    return raw[:8] if len(raw) >= 14 and raw[:8].isdigit() else fallback_day.replace("-", "")


def _response_day_minutes(rows: object, day: str) -> set[str]:
    if not isinstance(rows, list):
        return set()
    minutes: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("cntr_tm", "")).strip()
        if _raw_bar_date(row, day) != day.replace("-", ""):
            continue
        clock = raw[8:14] if len(raw) == 14 else raw if len(raw) == 6 else ""
        if len(clock) == 6 and clock.isdigit() and int(clock[:2]) < 24 and int(clock[2:4]) < 60:
            minutes.add(f"{clock[:2]}:{clock[2:4]}")
    return minutes


class _AsyncBrokerChartAdapter:
    """동기 계산기를 NAS의 단일 비동기 broker queue에 연결한다."""

    def __init__(self, broker: CentralRestBroker, loop: asyncio.AbstractEventLoop) -> None:
        self._broker, self._loop = broker, loop

    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *,
        cont_yn: str = "N", next_key: str = "",
    ) -> tuple[dict[str, Any], bool, str]:
        request = self._broker.request_unrecorded if api_id == "ka10099" else self._broker.request
        future = asyncio.run_coroutine_threadsafe(
            request(
                api_id, path, body, cont_yn=cont_yn, next_key=next_key,
            ),
            self._loop,
        )
        result = future.result()
        return result.payload, result.has_next, result.next_key
