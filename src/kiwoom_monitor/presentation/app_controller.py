from __future__ import annotations

from kiwoom_monitor.domain.time_basis import as_kst, kst_wall_now

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal

from kiwoom_monitor.application.ranking_execution import RankingExecutionCoordinator
from kiwoom_monitor.application.ranking_schedule import RankingChangeSummary, RankingResponseAction
from kiwoom_monitor.application.realtime_subscription import (
    RealtimeSubscriptionAction, RealtimeSubscriptionCoordinator,
)
from kiwoom_monitor.application.market_session_schedule import (
    is_nxt_only_session, next_realtime_session_boundary,
)
from kiwoom_monitor.application.secondary_data_schedule import (
    SecondaryDataFollowupCoordinator, SecondaryStartPhase,
)

from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
from kiwoom_monitor.infrastructure.persistence.market_cache_writer import MarketCacheWriter
from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.presentation.daily_high_worker_controller import DailyHighWorkerController
from kiwoom_monitor.presentation.fundamentals_worker_controller import FundamentalsWorkerController
from kiwoom_monitor.presentation.google_drive_worker_controller import GoogleDriveWorkerController
from kiwoom_monitor.presentation.historical_high_worker_controller import HistoricalHighWorkerController
from kiwoom_monitor.presentation.image_theme_ocr_worker_controller import ImageThemeOcrWorkerController
from kiwoom_monitor.presentation.krx_stock_catalog_worker_controller import KrxStockCatalogWorkerController
from kiwoom_monitor.presentation.minute_history_worker_controller import MinuteHistoryWorkerController
from kiwoom_monitor.presentation.nxt_eligibility_worker_controller import NxtEligibilityWorkerController
from kiwoom_monitor.presentation.ranking_worker_controller import RankingWorkerController
from kiwoom_monitor.presentation.realtime_worker_controller import RealtimeWorkerController
from kiwoom_monitor.presentation.top20_market_repair_worker_controller import Top20MarketRepairWorkerController
from kiwoom_monitor.presentation.update_worker_controller import UpdateWorkerController

if TYPE_CHECKING:
    from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
    from kiwoom_monitor.infrastructure.kiwoom_rest.daily_high_worker import DailyHighWorker
    from kiwoom_monitor.infrastructure.kiwoom_rest.fundamentals_worker import FundamentalsWorker
    from kiwoom_monitor.infrastructure.kiwoom_rest.historical_high_worker import HistoricalHighWorker
    from kiwoom_monitor.infrastructure.kiwoom_rest.minute_history_worker import MinuteHistoryWorker
    from kiwoom_monitor.infrastructure.kiwoom_rest.nxt_eligibility_worker import NxtEligibilityWorker
    from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker
    from kiwoom_monitor.infrastructure.kiwoom_rest.ranking_worker import RankingLoader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppShutdownActions:
    """UI-owned effects; their ordering and lifetime belong to AppController."""

    timers: tuple[QTimer, ...]
    tick_timer: Callable[[], QTimer | None]
    save_view_state: Callable[[], None]
    stop_visual_work: Callable[[], None]
    start_exit_backup_if_needed: Callable[[], None]
    flush_partial_top20: Callable[[], None]
    stop_auxiliaries: Callable[[], None]


@dataclass(frozen=True)
class AppRuntimeActions:
    """Apply the UI-owned caches and aggregator after the runtime is replaced."""

    apply_runtime: Callable[[dict[str, object]], None]
    capture_state: Callable[[], Callable[[], None]] | None = None


@dataclass(frozen=True)
class AppRankingActions:
    query_type: Callable[[], str]
    environment: Callable[[], str]
    has_blocking_modal: Callable[[], bool]
    apply_ranking: Callable[[tuple[object, ...], RankingChangeSummary, tuple[str, ...]], None]
    realtime_codes: Callable[[tuple[str, ...]], tuple[str, ...]]
    uses_nas_source: Callable[[], bool]
    start_nxt_eligibility: Callable[[tuple[str, ...]], bool]
    start_minute_history: Callable[[tuple[str, ...]], bool]


@dataclass(frozen=True)
class SecondaryLoadingRequest:
    codes: tuple[str, ...]
    finalization_codes: tuple[str, ...] = ()
    finalization_date: date | None = None
    after_hours_pause: bool = False
    weekend_daily_high_codes: tuple[str, ...] = ()
    stored_daily_high_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppSecondaryActions:
    prepare: Callable[[tuple[str, ...]], SecondaryLoadingRequest]
    phase_started: Callable[[SecondaryLoadingRequest, SecondaryStartPhase], None]
    start_minute_history: Callable[[tuple[str, ...], bool], bool]
    start_daily_high: Callable[[tuple[str, ...], bool], bool]
    start_fundamentals: Callable[[tuple[str, ...]], bool]
    start_nxt_eligibility: Callable[[tuple[str, ...]], bool]
    start_catalog: Callable[[], None]


class AppController(QObject):
    """Own application workers and stop producers before draining persistence."""

    shutdown_progress = Signal(str)
    shutdown_ready = Signal()
    minute_cache_saved = Signal()
    ranking_requested = Signal()
    api_reload_started = Signal()
    api_reload_status = Signal(str)
    api_reload_failed = Signal(str)
    api_reload_completed = Signal()
    ranking_started = Signal()
    ranking_finished = Signal()
    ranking_failed = Signal(str)
    ranking_waiting = Signal(int, int, bool, bool)
    ranking_deferred = Signal()
    nxt_preparing = Signal(int)
    realtime_status = Signal(str)
    background_failed = Signal(str)
    realtime_starting = Signal()

    def __init__(
        self, parent: QObject | None = None, *,
        realtime_worker_factory: Callable[[tuple[str, ...]], RealtimeTradeWorker] | None = None,
        minute_history_worker_factory: Callable[[tuple[str, ...]], MinuteHistoryWorker] | None = None,
        fundamentals_worker_factory: Callable[[tuple[str, ...]], FundamentalsWorker] | None = None,
        daily_high_worker_factory: Callable[[tuple[str, ...]], DailyHighWorker] | None = None,
        historical_high_worker_factory: Callable[[tuple[str, ...]], HistoricalHighWorker] | None = None,
        nxt_eligibility_worker_factory: Callable[[tuple[str, ...]], NxtEligibilityWorker] | None = None,
        monitor_database_path: Path | None = None,
        minute_bar_repository: MinuteBarRepository | None = None,
        stock_lookup: object | None = None,
        journal_database_path: Path | None = None,
        news_database_path: Path | None = None,
        entry_investor_loader: Callable[[str, datetime], dict[str, object]] | None = None,
        program_trade_loader: Callable[[str, date], tuple[dict[str, object], ...]] | None = None,
        ranking_loader: RankingLoader | None = None,
        api_runtime_factory: Callable[[], dict[str, object]] | None = None,
        market_data_client: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.ranking = RankingWorkerController(self)
        self.realtime = RealtimeWorkerController(self, realtime_worker_factory)
        self.minute_history = MinuteHistoryWorkerController(self, minute_history_worker_factory)
        self.fundamentals = FundamentalsWorkerController(self, fundamentals_worker_factory)
        self.daily_high = DailyHighWorkerController(self, daily_high_worker_factory)
        self.historical_high = HistoricalHighWorkerController(self, historical_high_worker_factory)
        self.nxt_eligibility = NxtEligibilityWorkerController(self, nxt_eligibility_worker_factory)
        self.krx_catalog = KrxStockCatalogWorkerController(self)
        self.image_ocr = ImageThemeOcrWorkerController(self)
        self.top20_repair = Top20MarketRepairWorkerController(self)
        self.google_drive = GoogleDriveWorkerController(self)
        self.updates = UpdateWorkerController(self)
        self.market_cache_writer = (
            MarketCacheWriter(monitor_database_path, now_provider=self.ranking_now)
            if monitor_database_path is not None else None
        )
        self.entry_snapshot_writer = (
            EntrySnapshotWriter(journal_database_path, news_database_path,
                                entry_investor_loader, program_trade_loader)
            if journal_database_path is not None else None
        )
        for writer in (self.market_cache_writer, self.entry_snapshot_writer):
            if writer is not None:
                writer.setParent(self)
        self.minute_bar_repository = minute_bar_repository
        self.stock_lookup = stock_lookup
        self.pending_prices: dict[str, int] = {}
        self.pending_highs: dict[tuple[date, str], int] = {}
        self._price_cache_receipt = 0
        self._price_cache_latest: dict[tuple[str, str], tuple[int, date, object]] = {}
        self._price_cache_retries: dict[tuple[int, datetime], tuple[dict, dict, dict, dict]] = {}
        self.pending_market_caps: dict[str, float] = {}
        self.pending_minutes: dict[tuple[str, datetime], MinuteOhlcv] = {}
        self.pending_minute_gaps: dict[str, tuple[datetime, ...]] = {}
        self.pending_minute_retention: date | None = None
        self._minute_retention_cutoff: date | None = None
        self.pending_market_minutes: dict[
            tuple[str, datetime], tuple[float, float, float, float, float | None]
        ] = {}
        self.price_cache_timer = QTimer(self)
        self.price_cache_timer.setSingleShot(True)
        self.price_cache_timer.setInterval(1_000)
        self.price_cache_timer.timeout.connect(self.flush_price_cache)
        self.minute_cache_timer = QTimer(self)
        self.minute_cache_timer.setSingleShot(True)
        self.minute_cache_timer.setInterval(1_000)
        self.minute_cache_timer.timeout.connect(self.flush_minute_cache)
        if self.market_cache_writer is not None:
            self.market_cache_writer.minute_saved.connect(self.minute_cache_saved.emit)
            self.market_cache_writer.minute_failed.connect(self.on_minute_cache_failed)
            self.market_cache_writer.gap_failed.connect(self.on_minute_gap_failed)
            self.market_cache_writer.price_failed.connect(self.on_price_cache_failed)
            self.market_cache_writer.retention_failed.connect(self.on_minute_retention_failed)
        self.top20_nas_workers: set[QThread] = set()
        self._started_writers: set[str] = set()
        self._closing = False
        self._shutdown_stage = "running"
        self._shutdown_cache_failed = False
        self._shutdown_cache_batch: dict[str, dict[object, object]] = {}
        self._shutdown_actions: AppShutdownActions | None = None
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._advance_shutdown)
        self.ranking_loader = ranking_loader
        self.market_data_client = market_data_client
        self.initial_ranking_waits_for_google_drive = False
        self._api_runtime_factory = api_runtime_factory
        self._api_reloading = False
        self._api_reload_stage = "idle"
        self._runtime_actions: AppRuntimeActions | None = None
        self._api_reload_timer = QTimer(self)
        self._api_reload_timer.setSingleShot(True)
        self._api_reload_timer.timeout.connect(self._advance_api_reload)
        self._initial_ranking_timer = QTimer(self)
        self._initial_ranking_timer.setSingleShot(True)
        self._initial_ranking_timer.timeout.connect(self._request_initial_ranking)
        self.ranking_execution = RankingExecutionCoordinator(self.ranking_now)
        self._ranking_actions: AppRankingActions | None = None
        self._secondary_actions: AppSecondaryActions | None = None
        self.secondary_data: SecondaryDataFollowupCoordinator | None = None
        self.realtime_subscription = RealtimeSubscriptionCoordinator(
            self.ranking_now, lambda: self._ranking_actions.environment() if self._ranking_actions else "real",
        )
        self.nxt_checked_codes: set[str] = set()
        self.nxt_enabled_codes: set[str] = set()
        self.ranked_codes: tuple[str, ...] = ()
        self.ranking_uses_local_fallback = False
        self._ranking_followup_revision = 0
        self._started_followup_revision = -1
        self._followup_timers: set[QTimer] = set()
        self.ranking_timer = QTimer(self)
        self.ranking_timer.setSingleShot(True)
        self.ranking_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.ranking_timer.timeout.connect(self.on_ranking_timer)
        self.ranking_preparation_timer = QTimer(self)
        self.ranking_preparation_timer.setSingleShot(True)
        self.ranking_preparation_timer.timeout.connect(self.prepare_ranking_refresh)
        self.realtime_session_timer = QTimer(self)
        self.realtime_session_timer.setSingleShot(True)
        self.realtime_session_timer.timeout.connect(self.on_realtime_session_boundary)
        self._ranking_retry_timer = QTimer(self)
        self._ranking_retry_timer.setSingleShot(True)
        self._ranking_retry_timer.timeout.connect(self._request_initial_ranking)
        self._deferred_ranking_timer = QTimer(self)
        self._deferred_ranking_timer.setSingleShot(True)
        self._deferred_ranking_timer.timeout.connect(self.flush_deferred_ranking)
        self.ranking.completed.connect(self.handle_ranking_response)
        self.ranking.failed.connect(self.on_ranking_failed)
        self.ranking.finished.connect(self.on_ranking_worker_finished)
        self.realtime.subscription_ready.connect(self.start_realtime_followups)
        self.nxt_eligibility.finished.connect(self._start_catalog_after_nxt)
        self.nxt_eligibility.finished.connect(lambda: self.start_realtime_subscription(self.ranked_codes))
        self.nxt_eligibility.finished.connect(lambda: self.start_realtime_followups(self.ranked_codes))
        self.minute_history.finished.connect(self._on_minute_history_finished)

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def api_reloading(self) -> bool:
        return self._api_reloading

    def configure_runtime(self, actions: AppRuntimeActions) -> None:
        if self._runtime_actions is not None or self.closing:
            raise RuntimeError("application runtime is already configured")
        self._runtime_actions = actions

    def configure_ranking(self, actions: AppRankingActions, secondary: AppSecondaryActions) -> None:
        if self._ranking_actions is not None or self.closing:
            raise RuntimeError("application ranking is already configured")
        self._ranking_actions = actions
        self._secondary_actions = secondary
        self.secondary_data = SecondaryDataFollowupCoordinator(
            start_minute_history=secondary.start_minute_history,
            start_daily_high=secondary.start_daily_high,
            start_daily_high_phase=self.start_daily_high_phase,
            start_fundamentals=secondary.start_fundamentals,
            start_nxt_phase=self.start_nxt_phase,
        )

    def ranking_now(self) -> datetime:
        provider = getattr(self.ranking_loader, "server_now", None)
        value = provider() if callable(provider) else None
        return value if isinstance(value, datetime) else kst_wall_now()

    def request_ranking(self) -> None:
        if self.closing or self.api_reloading or self.ranking_loader is None or self.ranking.is_running:
            return
        self.ranking_started.emit()
        logger.info("순위 조회 작업 시작: %s", self.ranking_now().strftime("%H:%M:%S"))
        self.ranking.start(self.ranking_loader)

    def on_ranking_timer(self) -> None:
        if self.closing or self.api_reloading:
            return
        now = self.ranking_now()
        if not self.ranking_execution.ranking_timer_fired(worker_running=self.ranking.is_running):
            logger.warning("순위 기준 시각 %s: 이전 순위 조회가 진행 중이라 완료 직후 재조회합니다.", now.strftime("%H:%M:%S"))
            return
        logger.info("순위 기준 시각 %s: 순위 조회를 시작합니다.", now.strftime("%H:%M:%S"))
        self.ranking_requested.emit()

    def prepare_ranking_refresh(self) -> None:
        if self.closing or self.api_reloading:
            return
        self.ranking_execution.begin_priority_preparation()
        for worker in (self.minute_history.worker, self.daily_high.worker,
                       self.fundamentals.worker, self.nxt_eligibility.worker,
                       self.krx_catalog.worker):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()

    def schedule_next_ranking_refresh(self) -> None:
        if self.closing or self.api_reloading or self.ranking_loader is None:
            return
        actions = self._ranking_actions
        assert actions is not None
        query_type = actions.query_type()
        schedule = self.ranking_execution.next_schedule(query_type)
        self.ranking_timer.start(schedule.delay_ms)
        self.ranking_preparation_timer.start(schedule.preparation_delay_ms)
        logger.info("다음 순위 조회 예약: %s + 0.25초 (기준 %s)",
                    schedule.next_time.strftime("%H:%M:%S"), query_type)

    def on_ranking_worker_finished(self) -> None:
        self.ranking_finished.emit()
        if self.closing or self.api_reloading or not self.ranking_execution.worker_finished():
            return
        logger.info("밀린 순위 조회를 즉시 시작합니다.")
        self._ranking_retry_timer.start(0)

    def on_ranking_failed(self, message: str) -> None:
        if self.api_reloading:
            logger.info("API 설정 교체 중 이전 순위 오류를 폐기합니다: %s", message)
            return
        self.ranking_execution.end_priority_preparation()
        logger.warning("순위 조회에 실패했습니다: %s", message)
        self.ranking_failed.emit(message)
        self.schedule_next_ranking_refresh()

    def handle_ranking_response(self, stocks: object) -> None:
        if self.api_reloading:
            logger.info("API 설정 교체 중 이전 순위 응답을 폐기합니다.")
            return
        if not isinstance(stocks, tuple):
            self.on_ranking_failed("순위 응답 형식이 올바르지 않습니다.")
            return
        actions = self._ranking_actions
        assert actions is not None
        previous_route = self.ranking_uses_local_fallback
        self.ranking_uses_local_fallback = bool(
            getattr(self.ranking_loader, "last_response_from_local_fallback", False)
        )
        if previous_route != self.ranking_uses_local_fallback:
            logger.info("순위 조회 경로 변경: %s", "이 PC 키움 API" if self.ranking_uses_local_fallback else "NAS")
        expected = int(getattr(self.ranking_loader, "EXPECTED_STOCKS", 0))
        stored = bool(getattr(self.ranking_loader, "last_response_from_storage", False))
        outcome = self.ranking_execution.handle_response(
            stocks, expected_count=expected,
            has_blocking_modal=actions.has_blocking_modal(), allow_partial_retry=not stored,
        )
        action = outcome.decision.action
        if action in {RankingResponseAction.RETRY_SOON, RankingResponseAction.WAIT_NEXT}:
            retry = action == RankingResponseAction.RETRY_SOON
            self.ranking_waiting.emit(len(stocks), expected, stored, retry)
            if retry:
                self._ranking_retry_timer.start(1_500)
            else:
                self.schedule_next_ranking_refresh()
            return
        if action == RankingResponseAction.DEFER_WHILE_MODAL:
            self.ranking_deferred.emit()
            self.schedule_next_ranking_refresh()
            self._schedule_deferred_ranking_flush()
            return
        summary = outcome.change_summary
        if summary is None:
            self.on_ranking_failed("순위 변경 계산 결과가 없습니다.")
            return
        started = time.monotonic()
        codes = tuple(stock.code for stock in stocks)
        self.ranked_codes = codes
        self._ranking_followup_revision += 1
        actions.apply_ranking(stocks, summary, codes)
        if is_nxt_only_session(actions.environment(), self.ranking_now()) and actions.start_nxt_eligibility(codes):
            self.nxt_preparing.emit(len(codes))
        else:
            if actions.uses_nas_source():
                actions.start_minute_history(codes)
            self.start_realtime_subscription(actions.realtime_codes(codes))
            self._schedule_followup_fallback(codes)
        self.schedule_next_ranking_refresh()
        logger.info("순위 표 적용 완료: %dms · %d개", round((time.monotonic() - started) * 1000), len(stocks))

    def _schedule_deferred_ranking_flush(self) -> None:
        if not self._deferred_ranking_timer.isActive():
            self._deferred_ranking_timer.start(150)

    def flush_deferred_ranking(self) -> None:
        if self.closing or self.api_reloading or not self.ranking_execution.has_deferred_response:
            return
        actions = self._ranking_actions
        assert actions is not None
        if actions.has_blocking_modal():
            self._schedule_deferred_ranking_flush()
            return
        stocks = self.ranking_execution.take_deferred_response()
        if stocks is not None:
            self.handle_ranking_response(stocks)

    def start_realtime_subscription(self, codes: tuple[str, ...]) -> None:
        if self.api_reloading:
            return
        worker = self.realtime.worker
        decision = self.realtime_subscription.plan(
            codes, self.nxt_enabled_codes, closing=self.closing,
            worker_available=self.realtime.available, worker_exists=worker is not None,
            worker_running=worker is not None and worker.isRunning(),
        )
        action = decision.action
        if action == RealtimeSubscriptionAction.NONE:
            return
        if action == RealtimeSubscriptionAction.STOP:
            self.realtime.stop()
            self.realtime_subscription.commit(decision)
            self.realtime_status.emit("현재 시간에는 수신 가능한 실시간 체결 종목이 없습니다.")
            return
        if action == RealtimeSubscriptionAction.UPDATE:
            self.realtime.update_codes(decision.active_codes, decision.nxt_codes)
            self.realtime_subscription.commit(decision)
            self.realtime_status.emit(f"실시간 연결 유지 · 구독 종목 변경 중 · {len(decision.active_codes)}종목")
            return
        if decision.stop_existing_first and not self.realtime.stop():
            self.background_failed.emit("이전 실시간 연결을 아직 종료하는 중입니다. 잠시 후 다시 시도합니다.")
            return
        self.realtime_starting.emit()
        if not self.realtime.start(decision.active_codes, decision.nxt_codes, followup_codes=codes):
            self.background_failed.emit("실시간 연결 작업을 시작하지 못했습니다.")
            return
        self.realtime_subscription.commit(decision)

    def schedule_realtime_session_refresh(self) -> None:
        if self.closing or self.api_reloading:
            return
        now = self.ranking_now()
        boundary = next_realtime_session_boundary(now)
        self.realtime_session_timer.start(max(100, round((boundary - now).total_seconds() * 1000)))

    def on_realtime_session_boundary(self) -> None:
        if self.closing or self.api_reloading:
            return
        self.start_realtime_subscription(self.ranked_codes)
        self.schedule_realtime_session_refresh()

    def _schedule_followup_fallback(self, codes: tuple[str, ...]) -> None:
        timer = QTimer(self)
        timer.setSingleShot(True)
        self._followup_timers.add(timer)
        def fallback() -> None:
            self._followup_timers.discard(timer)
            timer.deleteLater()
            self.start_realtime_followups(codes)
        timer.timeout.connect(fallback)
        timer.start(5_000)

    def start_realtime_followups(self, codes: tuple[str, ...]) -> None:
        if self.closing or self.api_reloading or self.ranking_execution.priority_preparing:
            return
        if not self.ranked_codes or not set(self.ranked_codes).issubset(codes):
            return
        revision = self._ranking_followup_revision
        if self._started_followup_revision == revision:
            return
        self.start_secondary_loading(codes)
        self._started_followup_revision = revision

    def start_secondary_loading(self, codes: tuple[str, ...]) -> None:
        if self.closing or self.api_reloading or self.ranking_execution.priority_preparing:
            return
        actions, coordinator = self._secondary_actions, self.secondary_data
        assert actions is not None and coordinator is not None
        request = actions.prepare(codes)
        phase = coordinator.start(
            request.codes, finalization_codes=request.finalization_codes,
            after_hours_pause=request.after_hours_pause,
            weekend_daily_high_codes=request.weekend_daily_high_codes,
            stored_daily_high_codes=request.stored_daily_high_codes,
        )
        actions.phase_started(request, phase)

    def start_daily_high_phase(self, codes: tuple[str, ...]) -> None:
        if self.closing or self.api_reloading or self.ranking_execution.priority_preparing:
            return
        assert self._secondary_actions is not None
        if not self._secondary_actions.start_daily_high(codes, False):
            self.start_fundamentals_phase(codes)

    def start_fundamentals_phase(self, codes: tuple[str, ...]) -> None:
        if self.closing or self.api_reloading or self.ranking_execution.priority_preparing:
            return
        assert self._secondary_actions is not None
        if not self._secondary_actions.start_fundamentals(codes):
            self.start_nxt_phase(codes)

    def start_nxt_phase(self, codes: tuple[str, ...]) -> None:
        if self.closing or self.api_reloading or self.ranking_execution.priority_preparing:
            return
        assert self._secondary_actions is not None
        if not self._secondary_actions.start_nxt_eligibility(codes):
            self._secondary_actions.start_catalog()

    def _start_catalog_after_nxt(self) -> None:
        if not self.closing and not self.api_reloading and self._secondary_actions is not None:
            self._secondary_actions.start_catalog()

    def _on_minute_history_finished(self, codes: tuple[str, ...], forced: bool) -> None:
        actions = self._secondary_actions
        if actions is None:
            return
        if forced:
            actions.start_daily_high(codes, True)
        else:
            self.start_daily_high_phase(codes)

    def _stop_ranking_timers(self) -> None:
        for timer in (self.ranking_timer, self.ranking_preparation_timer, self.realtime_session_timer,
                      self._ranking_retry_timer, self._deferred_ranking_timer, *self._followup_timers):
            timer.stop()
        for timer in self._followup_timers:
            timer.deleteLater()
        self._followup_timers.clear()

    def start_initial_ranking(self) -> None:
        if self.closing or self.api_reloading or self.ranking_loader is None:
            return
        actions = self._runtime_actions
        assert actions is not None
        self.initial_ranking_waits_for_google_drive = False
        self._initial_ranking_timer.start(0)
        self.schedule_next_ranking_refresh()
        self.schedule_realtime_session_refresh()

    def _request_initial_ranking(self) -> None:
        if not self.closing and not self.api_reloading and self.ranking_loader is not None:
            self.ranking_requested.emit()

    def _api_runtime_workers(self) -> tuple[QThread, ...]:
        return tuple(worker for worker in (
            self.realtime.worker, self.minute_history.worker, self.fundamentals.worker,
            self.daily_high.worker, self.historical_high.worker,
            self.nxt_eligibility.worker, self.ranking.worker,
        ) if worker is not None and worker.isRunning())

    def restart_for_api_settings(self) -> None:
        if self.closing or self.api_reloading:
            return
        if self._api_runtime_factory is None:
            self.api_reload_status.emit("API 설정이 저장되었습니다. 앱을 다시 열면 적용됩니다.")
            return
        actions = self._runtime_actions
        assert actions is not None
        self._api_reloading = True
        self._api_reload_stage = "workers"
        self._initial_ranking_timer.stop()
        self._stop_ranking_timers()
        self.ranking_execution.cancel_pending_request()
        self.ranking_execution.begin_priority_preparation()
        for worker in self._api_runtime_workers():
            worker.requestInterruption()
        self.api_reload_started.emit()
        self.api_reload_status.emit("새 API 설정을 적용하는 중입니다…")
        self._api_reload_timer.start(50)

    def _advance_api_reload(self) -> None:
        if self.closing or not self.api_reloading:
            return
        if self._api_runtime_workers():
            self._api_reload_stage = "workers"
            self.api_reload_status.emit("이전 API 작업을 정리하는 중입니다…")
            self._api_reload_timer.start(100)
            return
        if self._api_reload_stage == "workers":
            # Keep the old runtime closed to new results through a queued-signal turn.
            self._api_reload_stage = "signals"
            self._api_reload_timer.start(0)
            return
        actions = self._runtime_actions
        assert actions is not None
        try:
            assert self._api_runtime_factory is not None
            runtime = self._api_runtime_factory()
            self._apply_api_runtime(runtime, actions)
        except Exception as error:
            self._api_reloading = False
            self._api_reload_stage = "idle"
            self.ranking_execution.end_priority_preparation()
            self.api_reload_failed.emit(str(error))
            return
        self._api_reloading = False
        self._api_reload_stage = "idle"
        self.ranking_execution.end_priority_preparation()
        self.api_reload_completed.emit()
        self.start_initial_ranking()

    def _apply_api_runtime(self, runtime: dict[str, object], actions: AppRuntimeActions) -> None:
        controllers = (
            (self.realtime, "realtime_worker_factory"),
            (self.minute_history, "minute_history_worker_factory"),
            (self.fundamentals, "fundamentals_worker_factory"),
            (self.daily_high, "daily_high_worker_factory"),
            (self.historical_high, "historical_high_worker_factory"),
            (self.nxt_eligibility, "nxt_eligibility_worker_factory"),
        )
        previous = {
            "ranking_loader": self.ranking_loader, "market_data_client": self.market_data_client,
            **{name: controller._worker_factory for controller, name in controllers},
        }
        writer = self.entry_snapshot_writer
        investor_cache = None
        if writer is not None:
            previous.update(entry_investor_loader=writer._investor_loader, program_trade_loader=writer._program_loader)
            investor_cache = dict(writer._investor_cache)
        old_fallback = self.ranking_uses_local_fallback
        restore_view = actions.capture_state() if actions.capture_state is not None else None
        try:
            self.ranking_loader = runtime.get("ranking_loader")
            for controller, name in controllers:
                controller.set_factory(runtime.get(name))
            if writer is not None:
                writer.set_investor_loader(runtime.get("entry_investor_loader"))
                writer.set_program_loader(runtime.get("program_trade_loader"))
            self.market_data_client = runtime.get("market_data_client")
            self.ranking_uses_local_fallback = False
            actions.apply_runtime(runtime)
        except Exception as error:
            self.ranking_loader = previous["ranking_loader"]
            self.market_data_client = previous["market_data_client"]
            self.ranking_uses_local_fallback = old_fallback
            failures = []
            for controller, name in controllers:
                try:
                    controller.set_factory(previous[name])
                except Exception as restore_error:
                    failures.append(f"{name}: {restore_error}")
            if writer is not None:
                try:
                    writer.set_investor_loader(previous["entry_investor_loader"])
                    writer.set_program_loader(previous["program_trade_loader"])
                    writer._investor_cache.update(investor_cache or {})
                except Exception as restore_error:
                    failures.append(f"entry_snapshot_writer: {restore_error}")
            if restore_view is not None:
                try:
                    restore_view()
                except Exception as restore_error:
                    failures.append(f"view: {restore_error}")
            if failures:
                raise RuntimeError(f"{error}; runtime restore failed: {'; '.join(failures)}") from error
            raise
        self.realtime_subscription.reset()

    def flush_minute_cache(self) -> None:
        """실시간 체결을 1초 단위로 묶어 SQLite에 저장한다."""
        if not self.pending_minutes and not self.pending_market_minutes and not self.pending_minute_gaps and self.pending_minute_retention is None:
            return
        if self.minute_bar_repository is None:
            return
        pending, self.pending_minutes = self.pending_minutes, {}
        gaps, self.pending_minute_gaps = self.pending_minute_gaps, {}
        if self.market_cache_writer is not None and gaps:
            self.market_cache_writer.enqueue_gap_minutes(gaps)
        elif gaps:
            try:
                self.minute_bar_repository.mark_gap_minutes(gaps)
            except Exception as error:
                self.on_minute_gap_failed(gaps, str(error))
        market_pending, self.pending_market_minutes = self.pending_market_minutes, {}
        cutoff, self.pending_minute_retention = self.pending_minute_retention, None
        if self.market_cache_writer is not None:
            self.market_cache_writer.enqueue_minute_bars(pending, market_pending)
            if cutoff is not None:
                self.market_cache_writer.enqueue_minute_retention(cutoff)
            return
        if pending or market_pending or gaps:
            grouped: dict[str, list[MinuteOhlcv]] = {}
            for (code, _), bar in pending.items():
                grouped.setdefault(code, []).append(bar)
            try:
                self.minute_bar_repository.upsert_many(
                    {code: tuple(bars) for code, bars in grouped.items()}
                )
                self.minute_bar_repository.upsert_market_index_minutes(market_pending)
                self.minute_cache_saved.emit()
            except Exception as error:
                self.on_minute_cache_failed(pending, market_pending, str(error))
        if cutoff is None or (
            self._minute_retention_cutoff is not None and cutoff < self._minute_retention_cutoff
        ):
            self.enforce_minute_retention(chain(
                (bar.minute for bar in pending.values()),
                (minute for _, minute in market_pending),
                (minute for minutes in gaps.values() for minute in minutes),
            ))
        if cutoff is not None:
            self._purge_minute_retention(cutoff)

    def _purge_minute_retention(self, cutoff: date) -> None:
        try:
            self.minute_bar_repository.purge_before(cutoff)
        except Exception as error:
            self.on_minute_retention_failed(cutoff, str(error))
        else:
            previous = self._minute_retention_cutoff
            self._minute_retention_cutoff = max(previous, cutoff) if previous is not None else cutoff

    def enforce_minute_retention(self, minutes: Iterable[datetime]) -> None:
        cutoff = self._minute_retention_cutoff
        if cutoff is not None and any(minute.date() < cutoff for minute in minutes):
            self._purge_minute_retention(cutoff)

    def queue_minute_retention(self, cutoff: date) -> None:
        previous = self.pending_minute_retention
        self.pending_minute_retention = max(previous, cutoff) if previous is not None else cutoff
        self.flush_minute_cache()

    def on_minute_retention_failed(self, cutoff: date, message: str) -> None:
        previous = self.pending_minute_retention
        self.pending_minute_retention = max(previous, cutoff) if previous is not None else cutoff
        if self.closing:
            self._shutdown_cache_failed = True
        elif not self.minute_cache_timer.isActive():
            self.minute_cache_timer.start()
        logger.warning("오래된 분봉 DB 정리 실패: %s", message)

    def on_minute_cache_failed(
        self, pending: object, market_pending: object, message: str,
    ) -> None:
        pending = self._shutdown_failed_values("pending_minutes", pending)
        market_pending = self._shutdown_failed_values("pending_market_minutes", market_pending)
        if isinstance(pending, dict):
            self.pending_minutes = {**pending, **self.pending_minutes}
        if isinstance(market_pending, dict):
            self.pending_market_minutes = {
                **market_pending, **self.pending_market_minutes,
            }
        if not self.closing and not self.minute_cache_timer.isActive():
            self.minute_cache_timer.start()
        logger.warning("실시간 분봉 DB 저장 실패: %s", message)

    def queue_minute_gaps(self, gaps: dict[str, tuple[datetime, ...]]) -> None:
        for code, minutes in gaps.items():
            if code.startswith("market:") and (not minutes or code in self.pending_minute_gaps and not self.pending_minute_gaps[code]):
                self.pending_minute_gaps[code] = ()
            else:
                self.pending_minute_gaps[code] = tuple(sorted(set(minutes) | set(self.pending_minute_gaps.get(code, ()))))
        self.flush_minute_cache()

    def on_minute_gap_failed(self, gaps: object, message: str) -> None:
        if isinstance(gaps, dict):
            for code, minutes in gaps.items():
                if code.startswith("market:") and (not minutes or code in self.pending_minute_gaps and not self.pending_minute_gaps[code]):
                    self.pending_minute_gaps[code] = ()
                else:
                    self.pending_minute_gaps[code] = tuple(sorted(set(minutes) | set(self.pending_minute_gaps.get(code, ()))))
        if self.closing:
            self._shutdown_cache_failed = True
        elif not self.minute_cache_timer.isActive():
            self.minute_cache_timer.start()
        logger.warning("분봉 누락 품질 저장 실패: %s", message)

    def flush_price_cache(self) -> None:
        """Dispatch one normal batch; retain failed jobs' original clock and high dates."""
        jobs = list(self._price_cache_retries.values())
        self._price_cache_retries.clear()
        normal = None
        if self.pending_prices or self.pending_highs or self.pending_market_caps:
            observed_at = as_kst(self.ranking_now())
            self._price_cache_receipt += 1
            context = {"trade_date": observed_at.date(), "observed_at": observed_at,
                       "receipt": self._price_cache_receipt}
            normal = (self.pending_prices,
                      {(key if isinstance(key, tuple) else (observed_at.date(), key)): value
                       for key, value in self.pending_highs.items()},
                      self.pending_market_caps, context)
            self.pending_prices, self.pending_highs, self.pending_market_caps = {}, {}, {}
            for field, values in zip(("price", "high", "cap"), normal[:3]):
                for key, value in values.items():
                    day, code = key if field == "high" else (observed_at.date(), key)
                    self._price_cache_latest[field, code] = (self._price_cache_receipt, day, value)
        for prices, highs, market_caps, context in jobs:
            prices, highs, market_caps = self._price_cache_uncovered(prices, highs, market_caps, context)
            if prices or highs or market_caps:
                self._dispatch_price_cache(prices, highs, market_caps, context)
        if normal is not None:
            self._dispatch_price_cache(*normal)

    def _dispatch_price_cache(self, prices: dict, highs: dict, market_caps: dict,
                              context: dict) -> None:
        if self.market_cache_writer is not None:
            self.market_cache_writer.enqueue_price_cache(
                prices, highs, market_caps, context["trade_date"],
                observed_at=context["observed_at"], receipt=context["receipt"],
            )
            return
        try:
            if self.stock_lookup is not None and hasattr(self.stock_lookup, "update_price_cache"):
                self.stock_lookup.update_price_cache(
                    prices, highs, market_caps, context["trade_date"],
                    observed_at=context["observed_at"],
                )
            else:
                # Legacy lookups retain their existing individual method contracts.
                if self.stock_lookup is not None and hasattr(self.stock_lookup, "update_last_prices"):
                    self.stock_lookup.update_last_prices(prices)
                if self.stock_lookup is not None and hasattr(self.stock_lookup, "update_last_market_caps"):
                    self.stock_lookup.update_last_market_caps(market_caps)
                if self.stock_lookup is not None and hasattr(self.stock_lookup, "update_intraday_highs"):
                    grouped: dict[date, dict[str, int]] = {}
                    for (day, code), value in highs.items():
                        grouped.setdefault(day, {})[code] = value
                    for day, values in grouped.items():
                        self.stock_lookup.update_intraday_highs(values, day)
        except Exception as error:
            self.on_price_cache_failed(prices, highs, market_caps, context, str(error))

    def _price_cache_uncovered(self, prices: dict, highs: dict, market_caps: dict,
                               context: dict) -> tuple[dict, dict, dict]:
        result = []
        for field, values, pending in (("price", prices, self.pending_prices),
                                       ("high", highs, self.pending_highs),
                                       ("cap", market_caps, self.pending_market_caps)):
            retained = {}
            for key, value in values.items():
                day, code = key if field == "high" else (context["trade_date"], key)
                latest = self._price_cache_latest.get((field, code))
                # Legacy date-only failures carry no ordering receipt. Pending
                # values still take precedence, but dispatch order is unknowable.
                newer = context["receipt"] > 0 and latest is not None and latest[0] > context["receipt"]
                covered = newer and (field != "high" or latest[1] == day and latest[2] >= value)
                pending_value = pending.get(key)
                if not covered and (pending_value is None or field == "high" and pending_value < value):
                    retained[key] = value
            result.append(retained)
        return tuple(result)

    def on_price_cache_failed(
        self, prices: object, highs: object, market_caps: object,
        trade_date: object, message: str,
    ) -> None:
        if isinstance(trade_date, dict):
            context = trade_date
        else:
            # Old five-argument emitters may provide only a date (or no usable date).
            observed_at = as_kst(self.ranking_now())
            context = {"trade_date": trade_date if isinstance(trade_date, date) else observed_at.date(),
                       "observed_at": observed_at, "receipt": 0}
        prices = dict(prices) if isinstance(prices, dict) else {}
        highs = {(key if isinstance(key, tuple) else (context["trade_date"], key)): value
                 for key, value in highs.items()} if isinstance(highs, dict) else {}
        market_caps = dict(market_caps) if isinstance(market_caps, dict) else {}
        prices, highs, market_caps = self._price_cache_uncovered(prices, highs, market_caps, context)
        key = (context["receipt"], context["observed_at"])
        previous = self._price_cache_retries.get(key)
        if previous is not None:
            prices = {**previous[0], **prices}
            highs = {**previous[1], **{k: max(v, previous[1].get(k, v)) for k, v in highs.items()}}
            market_caps = {**previous[2], **market_caps}
        if prices or highs or market_caps:
            self._price_cache_retries[key] = (prices, highs, market_caps, context)
        # Coalesce by field/code and day/code, never by failure-attempt count.
        winners = {}
        for batch_key, batch in self._price_cache_retries.items():
            for field, values in enumerate(batch[:3]):
                for item, value in values.items():
                    identity = (field, item)
                    rank = (value, batch[3]["receipt"]) if field == 1 else (batch[3]["receipt"],)
                    if identity not in winners or rank >= winners[identity][0]:
                        winners[identity] = (rank, batch_key)
        retained_batches = {}
        for batch_key, batch in self._price_cache_retries.items():
            retained = []
            for field, values in enumerate(batch[:3]):
                retained.append({item: value for item, value in values.items()
                                 if winners[field, item][1] == batch_key})
            if any(retained):
                retained_batches[batch_key] = (*retained, batch[3])
        self._price_cache_retries = retained_batches
        if self.closing and self._price_cache_retries:
            self._shutdown_cache_failed = True
        elif not self.closing and self._price_cache_retries and not self.price_cache_timer.isActive():
            self.price_cache_timer.start()
        logger.warning("현재가 캐시 저장 실패: %s", message)

    def _shutdown_failed_values(self, name: str, values: object) -> object:
        if not isinstance(values, dict) or not self.closing or self._shutdown_stage not in {
            "writers", "writer_signals",
        }:
            return values
        # A queued old failure may arrive after the final, newer batch commits.
        # Its overlapping keys are covered by that batch's own success/failure.
        latest = self._shutdown_cache_batch.get(name, {})
        failed = {key: value for key, value in values.items()
                  if key not in latest or value == latest[key]}
        if failed:
            self._shutdown_cache_failed = True
        return failed

    def start_market_cache_writer(self) -> None:
        self._start_writer("market_cache_writer")

    def queue_price_cache(self, code: str, *, price: int | None = None,
                          high: int | None = None, market_cap: float | None = None,
                          observed_at: datetime | None = None) -> None:
        if price is not None:
            self.pending_prices[code] = price
        if high is not None:
            key = (as_kst(observed_at if observed_at is not None else self.ranking_now()).date(), code)
            self.pending_highs[key] = max(high, self.pending_highs.get(key, high))
        if market_cap is not None:
            self.pending_market_caps[code] = market_cap
        if not self.closing and not self.price_cache_timer.isActive():
            self.price_cache_timer.start()

    def queue_minute_bar(self, code: str, bar: MinuteOhlcv) -> None:
        self.pending_minutes[(code, bar.minute)] = bar
        if not self.closing and not self.minute_cache_timer.isActive():
            self.minute_cache_timer.start()

    def queue_market_index(self, market: str, minute: datetime, value: float,
                           trade_value: float | None) -> None:
        key = (market, minute)
        previous = self.pending_market_minutes.get(key)
        self.pending_market_minutes[key] = (
            previous[0] if previous else value,
            max(previous[1], value) if previous else value,
            min(previous[2], value) if previous else value,
            value, trade_value,
        )
        if not self.closing and not self.minute_cache_timer.isActive():
            self.minute_cache_timer.start()

    def _stop_storage_timers(self) -> None:
        for timer in (self.price_cache_timer, self.minute_cache_timer):
            timer.stop()

    def start_entry_snapshot_writer(self) -> None:
        self._start_writer("entry_snapshot_writer")

    def _start_writer(self, name: str) -> None:
        writer = getattr(self, name)
        if writer is not None and name not in self._started_writers and not self.closing:
            self._started_writers.add(name)
            writer.start()

    def configure_shutdown(self, actions: AppShutdownActions) -> None:
        if self._shutdown_actions is not None or self.closing:
            raise RuntimeError("application shutdown is already configured")
        self._shutdown_actions = actions

    def add_shutdown_timer(self, timer: QTimer) -> None:
        actions = self._shutdown_actions
        if actions is None or self.closing:
            raise RuntimeError("application shutdown timers cannot be changed now")
        self._shutdown_actions = replace(actions, timers=(*actions.timers, timer))

    def _producer_workers(self) -> tuple[tuple[QThread | None, str], ...]:
        # Read current references on every check, including dynamically created NAS workers.
        workers = (
            (self.realtime.worker, "실시간 체결"),
            (self.minute_history.worker, "분봉 보완"),
            (self.fundamentals.worker, "기본정보"),
            (self.daily_high.worker, "신고가"),
            (self.historical_high.worker, "역사적 신고가"),
            (self.nxt_eligibility.worker, "NXT 확인"),
            (self.ranking.worker, "실시간 순위"),
            (self.image_ocr.worker, "이미지 OCR"),
            (self.krx_catalog.worker, "종목 목록"),
            (self.top20_repair.worker, "TOP20 보완"),
            (self.google_drive.worker, "Google Drive"),
            (self.updates.check_worker, "업데이트 확인"),
            (self.updates.download_worker, "업데이트 다운로드"),
            *((worker, "NAS 시장자료") for worker in tuple(self.top20_nas_workers)),
        )
        referenced = {worker for worker, _ in workers}
        writers = {self.market_cache_writer, self.entry_snapshot_writer}
        # Drive may clear its public worker reference while its thread is still
        # returning from run(). Qt ownership keeps that thread visible until finished.
        return workers + tuple(
            (worker, "백그라운드 작업") for worker in self.findChildren(QThread)
            if worker not in referenced and worker not in writers
        )

    def request_close(self) -> bool:
        if self._shutdown_stage == "ready":
            return True
        if self._shutdown_stage == "cache_failed":
            writer = self.market_cache_writer
            if self._running_writers():
                return False
            # A fresh user close request retries the same stopped writer and RAM
            # buffers; producers stay stopped and normal save timers stay off.
            if writer is not None:
                writer.start()
            entry_writer = self.entry_snapshot_writer
            if entry_writer is not None and entry_writer.has_pending_work():
                entry_writer.start()
            self._drain_writers()
            if not self._running_writers():
                self._shutdown_stage = "writer_signals"
            self._shutdown_timer.start(100 if self._running_writers() else 0)
            return False
        if self.closing:
            return False
        actions = self._shutdown_actions
        if actions is None:
            raise RuntimeError("application shutdown is not configured")
        self._closing = True
        self._api_reload_timer.stop()
        self._initial_ranking_timer.stop()
        self._stop_ranking_timers()
        self._shutdown_stage = "producers"
        actions.save_view_state()
        self._stop_timers()
        actions.stop_visual_work()
        self.shutdown_progress.emit("종료 중: 실행 중인 작업을 일시 중지하고 있습니다…")
        actions.start_exit_backup_if_needed()
        self._interrupt_producers()
        if not self.image_ocr.stop_for_shutdown():
            logger.warning("OCR 보조 스레드가 강제 종료 제한시간 안에 끝나지 않았습니다.")
        # Even a finished thread may still have queued result/finished signals.
        if any(worker is not None for worker, _ in self._producer_workers()):
            self._shutdown_timer.start(100)
            return False
        self._drain_writers()
        if self._running_writers():
            self._shutdown_timer.start(100)
            return False
        if self._started_writers:
            self._shutdown_stage = "writer_signals"
            self._shutdown_timer.start(0)
            return False
        self._complete_shutdown(notify=False)
        return self._shutdown_stage == "ready"

    def _stop_timers(self) -> None:
        self._stop_storage_timers()
        actions = self._shutdown_actions
        assert actions is not None
        for timer in actions.timers:
            timer.stop()
        tick_timer = actions.tick_timer()
        if tick_timer is not None:
            tick_timer.stop()

    def _interrupt_producers(self) -> list[str]:
        labels = []
        for worker, label in self._producer_workers():
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                labels.append(label)
        return labels

    def _running_writers(self) -> tuple[QThread, ...]:
        return tuple(writer for writer in (self.market_cache_writer, self.entry_snapshot_writer)
                     if writer is not None and writer.isRunning())

    def _drain_writers(self) -> None:
        actions = self._shutdown_actions
        assert actions is not None
        retrying_cache = self._shutdown_stage == "cache_failed"
        self._shutdown_stage = "writers"
        self._shutdown_cache_failed = False
        # Queued producer results can restart a save timer while shutdown is waiting.
        self._stop_timers()
        if not retrying_cache:
            actions.flush_partial_top20()
        # Keep only this final batch, not an unbounded history of cache writes.
        self._shutdown_cache_batch = {
            name: dict(getattr(self, name)) for name in (
                "pending_minutes", "pending_market_minutes", "pending_minute_gaps",
            )
        }
        self.flush_price_cache()
        self.flush_minute_cache()
        if self.entry_snapshot_writer is not None:
            self.entry_snapshot_writer.requestInterruption()
        if self.market_cache_writer is not None and not self.market_cache_writer.stop_and_drain():
            logger.warning("실시간 캐시 저장 스레드가 종료 제한시간 안에 끝나지 않았습니다. 종료를 기다립니다.")

    def _advance_shutdown(self) -> None:
        if self._shutdown_stage == "producers":
            labels = self._interrupt_producers()
            if labels:
                self.shutdown_progress.emit(f"종료 중: {', '.join(labels)} 작업을 중단하는 중입니다…")
                self._shutdown_timer.start(250)
                return
            self._shutdown_stage = "producer_signals"
            self._shutdown_timer.start(0)
            return
        if self._shutdown_stage == "producer_signals":
            if self._interrupt_producers():
                self._shutdown_stage = "producers"
                self._shutdown_timer.start(250)
                return
            self._drain_writers()
        if self._shutdown_stage == "writers":
            if self._running_writers():
                self.shutdown_progress.emit("종료 중: 마지막 자료 저장을 기다리고 있습니다…")
                self._shutdown_timer.start(250)
                return
            self._shutdown_stage = "writer_signals"
            self._shutdown_timer.start(0)
            return
        if self._shutdown_stage == "writer_signals":
            self._complete_shutdown(notify=True)

    def _complete_shutdown(self, *, notify: bool) -> None:
        actions = self._shutdown_actions
        assert actions is not None
        entry_writer = self.entry_snapshot_writer
        if (entry_writer is not None and entry_writer.has_pending_work()) or self._price_cache_retries or self.pending_minute_retention is not None or (
            self._shutdown_cache_failed and (
                self.pending_minutes or self.pending_market_minutes or self.pending_minute_gaps
            )
        ):
            self._shutdown_stage = "cache_failed"
            self.shutdown_progress.emit(
                "마지막 자료 저장에 실패했습니다. 자료를 유지하고 있습니다. "
                "문제를 해결한 뒤 닫기를 다시 시도해 주세요."
            )
            return
        actions.stop_auxiliaries()
        self._shutdown_stage = "ready"
        if notify:
            self.shutdown_ready.emit()
