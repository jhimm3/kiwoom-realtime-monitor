from __future__ import annotations

import json
import logging
import os
import ctypes
import hashlib
import re
import shutil
import secrets
import subprocess
import sys
import time
from html import escape
from dataclasses import replace
from pathlib import Path
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime, time as clock_time, timedelta
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PySide6.QtGui import QBrush, QCloseEvent, QResizeEvent, QShowEvent, QColor, QDesktopServices, QFontMetrics, QIcon, QKeySequence, QPainter, QPen, QPolygon, QPalette
from PySide6.QtCore import QDate, QEvent, QEventLoop, QSettings, QThread, QTimer, QUrl, QSize, QPoint, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QColorDialog,
    QCheckBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QTextEdit,
    QDialog,
    QDialogButtonBox,
    QDateEdit,
    QFormLayout,
    QGridLayout,
    QFileDialog,
    QFrame,
    QMessageBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressDialog,
    QMenu,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QDoubleSpinBox,
    QSpinBox,
    QStackedLayout,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QToolTip,
    QLayout,
    QVBoxLayout,
    QWidget,
)


from PySide6.QtCore import Qt
from PIL import Image, ImageOps, UnidentifiedImageError

from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.infrastructure.app_paths import AppPaths
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.central_operational_settings import CentralOperationalSettingsClient
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context
from kiwoom_monitor.infrastructure.persistence.database import DEFAULT_SETTINGS
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import MarketIndexTick, OrderExecution, TradeTick
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.minute_history_worker import MinuteHistoryWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.fundamentals_worker import FundamentalsWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.new_high_worker import NewHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.ranking_worker import RankingWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.daily_high_worker import DailyHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.historical_high_worker import HistoricalHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.nxt_eligibility_worker import NxtEligibilityWorker
from kiwoom_monitor.application.daily_high_service import DailyHighTargets
from kiwoom_monitor.application.high_price_policy import (
    merge_fundamentals_with_adjusted_high,
    selected_high_price,
)
from kiwoom_monitor.application.ranking_schedule import RankingResponseAction
from kiwoom_monitor.application.ranking_execution import RankingExecutionCoordinator
from kiwoom_monitor.application.realtime_subscription import (
    RealtimeSubscriptionAction,
    RealtimeSubscriptionCoordinator,
)
from kiwoom_monitor.application.market_data_finalization import (
    evaluate_finalization_outcome,
    finalization_candidates,
    finalization_target_date,
    minute_bars_complete,
)
from kiwoom_monitor.application.secondary_data_schedule import (
    SecondaryDataFollowupCoordinator,
    SecondaryStartPhase,
    daily_catalog_sync_due,
    daily_high_candidates,
    fundamentals_candidates,
    minute_history_candidates,
    nxt_eligibility_candidates,
)
from kiwoom_monitor.application.historical_high_service import HistoricalHighTarget
from kiwoom_monitor.application.trade_strength import StockFundamentals, trade_strength_percent
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSettingsRepository
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupError, SettingsBackupService
from kiwoom_monitor.infrastructure.central_settings_sync import is_shared_setting
from kiwoom_monitor.infrastructure.persistence.journal_backup import JournalBackupService
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import TradeEntrySnapshot
from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupError, ThemeBackupService
from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncError, GoogleDriveSyncService
from kiwoom_monitor.infrastructure.excel.theme_repository import ThemeRepository as ExcelThemeRepository
from kiwoom_monitor.domain.theme_import import validate_theme_header, validate_theme_rows
from kiwoom_monitor.domain.theme_parser import parse_themes, theme_key
from kiwoom_monitor.domain.theme_text_import import parse_theme_text
from kiwoom_monitor.presentation.theme_colors import text_color
from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
from kiwoom_monitor.presentation.app_metadata import (
    APP_COPYRIGHT,
    APP_DISPLAY_NAME,
    APP_VERSION,
    INVESTMENT_NOTICE,
)
from kiwoom_monitor.presentation.background_workers import (
    GoogleDriveSyncWorker,
    UpdateCheckWorker,
    UpdateDownloadWorker,
)
from kiwoom_monitor.presentation.daily_high_worker_controller import (
    DailyHighWorkerController,
)
from kiwoom_monitor.presentation.fundamentals_worker_controller import (
    FundamentalsWorkerController,
)
from kiwoom_monitor.presentation.google_drive_worker_controller import (
    GoogleDriveWorkerController,
)
from kiwoom_monitor.presentation.historical_high_worker_controller import (
    HistoricalHighWorkerController,
)
from kiwoom_monitor.presentation.column_manager_dialog import ColumnManagerDialog
from kiwoom_monitor.presentation.main_window_components import (
    AlertSettingsDialog,
    ClickableLabel,
    NxtMarkerDelegate,
    StockNameChangeReviewDialog,
)
from kiwoom_monitor.presentation.krx_stock_catalog_worker_controller import (
    KrxStockCatalogWorkerController,
)
from kiwoom_monitor.presentation.image_theme_ocr_worker_controller import (
    ImageThemeOcrWorkerController,
)
from kiwoom_monitor.presentation.main_table_formatting import (
    change_rate_text_color,
    decimal_places,
    format_market_cap_eok,
    format_trade_value_eok,
    rank_highlight_duration_ms,
    row_background_color,
    theme_trade_summary_html,
    trade_value_color,
)
from kiwoom_monitor.presentation.main_table_column_controller import (
    MainTableColumnController,
)
from kiwoom_monitor.presentation.main_window_layout import (
    clamp_uniform_row_height,
    fitted_window_size,
    parse_window_position,
    responsive_row_height,
)
from kiwoom_monitor.presentation.minute_history_worker_controller import (
    MinuteHistoryWorkerController,
)
from kiwoom_monitor.presentation.new_high_worker_controller import (
    NewHighWorkerController,
)
from kiwoom_monitor.presentation.nxt_eligibility_worker_controller import (
    NxtEligibilityWorkerController,
)
from kiwoom_monitor.presentation.process_control import (
    AuxiliaryProcessManager,
    build_auxiliary_command,
    JsonCommandChannel,
    JsonRequestInbox,
    process_identity_is_alive,
    read_process_identity,
)
from kiwoom_monitor.presentation.ranking_worker_controller import RankingWorkerController
from kiwoom_monitor.presentation.realtime_worker_controller import RealtimeWorkerController
from kiwoom_monitor.presentation.settings_dialog import (
    HIGH_PERIODS,
    SettingsDialog,
    selected_high_cycle_periods,
)
from kiwoom_monitor.presentation.similar_stock_dialog import (
    SimilarStockDialog,
    choose_similar_stock,
    confirm_pending_name_change,
)
from kiwoom_monitor.presentation.theme_dialogs import (
    ImageThemeImportOptionsDialog,
    ImageThemeRowsDialog,
    TextThemeImportDialog,
    ThemeBulkDeleteDialog,
    ThemeBulkEditDialog,
    ThemeColorDialog,
    ThemeEditDialog,
    ThemeManagerDialog,
    ThemePreviewDialog,
)
from kiwoom_monitor.presentation.top20_trade_value import (
    Top20MarketRepairWorker,
    Top20TradeValueChart,
    Top20TradeValueWindow,
)
from kiwoom_monitor.presentation.top20_market_repair_worker_controller import (
    Top20MarketRepairWorkerController,
)
from kiwoom_monitor.presentation.update_worker_controller import UpdateWorkerController
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import ApiProfiles, LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomApiError, KiwoomRestClient, KiwoomSettings
from kiwoom_monitor.domain.strength_level import strength_badge
from kiwoom_monitor.application.theme_matching import MatchedThemeRow, match_theme_rows
from kiwoom_monitor.application.theme_preview import preview_theme_changes
from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv, MinuteTradeValueAggregator
from kiwoom_monitor.application.market_session_schedule import (
    after_hours_data_pause,
    is_nxt_only_session,
    next_realtime_session_boundary,
    top20_collection_available,
    top20_collection_open,
)
from kiwoom_monitor.application.theme_ranking import (
    aggregate_theme_metrics,
    theme_group_sort_key,
    top_theme_trade_values,
    visible_theme_frequency,
)
from kiwoom_monitor.application.top20_trade_value_collector import (
    Top20MinuteRecord,
    Top20TradeValueCollector,
)
from kiwoom_monitor.infrastructure.news_ai import DEFAULT_MODELS, MODEL_OPTIONS
from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import ImageThemeOcrWorker
from kiwoom_monitor.infrastructure.krx.stock_catalog_worker import KrxStockCatalogWorker
from kiwoom_monitor.infrastructure.update_planner import UpdatePlan, UpdateStep, build_update_plan


class RankingLoader(Protocol):
    def load_top_stocks(self) -> tuple[object, ...]: ...


logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    TRADE_VALUE_ALERT_ROLE = Qt.ItemDataRole.UserRole + 3
    TRADE_VALUE_ALERT_COLOR = QColor("#F4CCCC")
    COLUMNS = (("rank","순위"),("stock","종목"),("themes","테마"),("change_rate","등락률"),("strength_1m","1분강도"),("current_price","현재가"),("trade_value_1m","1분"),("trade_value_5m","5분"),("trade_value_60m","60분"),("trade_value_day","1일"),("strength_5m","5분강도"),("strength_60m","60분강도"),("strength_day","1일강도"),("new_high_price","신고가"),("high_distance","신고가%"),("market_cap","시가총액"))
    HEADERS = tuple(label for _, label in COLUMNS)

    @property
    def _ranking_worker(self) -> RankingWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._ranking_worker_controller.worker

    @property
    def _realtime_worker(self) -> RealtimeTradeWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._realtime_worker_controller.worker

    @property
    def _minute_history_worker(self) -> MinuteHistoryWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._minute_history_worker_controller.worker

    @property
    def _daily_high_worker(self) -> DailyHighWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._daily_high_worker_controller.worker

    @property
    def _fundamentals_worker(self) -> FundamentalsWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._fundamentals_worker_controller.worker

    @property
    def _historical_high_worker(self) -> HistoricalHighWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._historical_high_worker_controller.worker

    @property
    def _nxt_eligibility_worker(self) -> NxtEligibilityWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._nxt_eligibility_worker_controller.worker

    @property
    def _new_high_worker(self) -> NewHighWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._new_high_worker_controller.worker

    @property
    def _krx_stock_catalog_worker(self) -> KrxStockCatalogWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._krx_stock_catalog_worker_controller.worker

    @property
    def _top20_repair_worker(self) -> Top20MarketRepairWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._top20_repair_worker_controller.worker

    @property
    def _image_theme_ocr_worker(self) -> ImageThemeOcrWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._image_theme_ocr_worker_controller.worker

    @property
    def _google_drive_worker(self) -> GoogleDriveSyncWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._google_drive_worker_controller.worker

    @property
    def _update_check_worker(self) -> UpdateCheckWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._update_worker_controller.check_worker

    @property
    def _update_download_worker(self) -> UpdateDownloadWorker | None:
        """기존 종료 처리와 GUI 회귀 테스트용 읽기 전용 호환 접근자."""
        return self._update_worker_controller.download_worker

    def __init__(
        self,
        settings: SettingsRepository,
        ranking_loader: RankingLoader | None = None,
        realtime_worker_factory: Callable[[tuple[str, ...]], RealtimeTradeWorker] | None = None,
        minute_history_worker_factory: Callable[[tuple[str, ...]], MinuteHistoryWorker] | None = None,
        fundamentals_worker_factory: Callable[[tuple[str, ...]], FundamentalsWorker] | None = None,
        minute_aggregator: MinuteTradeValueAggregator | None = None,
        minute_bar_repository: MinuteBarRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
        themes: dict[str, str] | None = None,
        columns: ColumnSettingsRepository | None = None,
        stock_lookup: object | None = None,
        theme_store: object | None = None,
        daily_high_worker_factory: Callable[[tuple[str, ...]], DailyHighWorker] | None = None,
        historical_high_worker_factory: Callable[[tuple[str, ...]], HistoricalHighWorker] | None = None,
        nxt_eligibility_worker_factory: Callable[[tuple[str, ...]], NxtEligibilityWorker] | None = None,
        google_drive_sync: GoogleDriveSyncService | None = None,
        initial_google_drive_download: bool = False,
        api_runtime_factory: Callable[[], dict[str, object]] | None = None,
        news_config_path: Path | None = None,
        news_database_path: Path | None = None,
        journal_database_path: Path | None = None,
        monitor_database_path: Path | None = None,
        entry_investor_loader: Callable[[str, datetime], dict[str, object]] | None = None,
        program_trade_loader: Callable[[str, date], tuple[dict[str, object], ...]] | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._ranking_loader = ranking_loader
        self._realtime_worker_factory = realtime_worker_factory
        self._minute_history_worker_factory = minute_history_worker_factory
        self._fundamentals_worker_factory = fundamentals_worker_factory
        self._daily_high_worker_factory = daily_high_worker_factory
        self._fundamentals: dict[str, StockFundamentals] = {}
        self._daily_highs: dict[str, DailyHighTargets] = {}
        self._historical_high_prices: dict[str, int] = {}
        self._previous_day_trade_values: dict[str, float] = {}
        self._previous_day_close_prices: dict[str, int] = {}
        self._daily_high_cache_date: date | None = None
        self._daily_high_basis_refresh_expected: set[str] = set()
        self._daily_high_basis_refresh_received: set[str] = set()
        self._historical_high_refresh_expected: set[str] = set()
        self._historical_high_refresh_received: set[str] = set()
        self._themes = themes or {}
        self._pending_price_cache: dict[str, int] = {}
        self._pending_today_high_cache: dict[str, int] = {}
        self._columns = columns
        self._stock_lookup = stock_lookup
        self._theme_store = theme_store
        self._google_drive_sync = google_drive_sync
        self._api_runtime_factory = api_runtime_factory
        # 뉴스는 별도 프로세스와 전용 DB로 실행해 실시간 표의 Qt 이벤트 루프와
        # SQLite 잠금을 공유하지 않는다.
        self._news_config_path = news_config_path
        self._news_database_path = news_database_path
        self._news_process_manager = AuxiliaryProcessManager()
        self._news_command_path = news_database_path.with_name("news_command.json") if news_database_path else None
        self._news_command_channel = JsonCommandChannel(self._news_command_path)
        self._news_state_path = news_database_path.with_name("news_window_state.json") if news_database_path else None
        self._news_api_settings_dialog: QDialog | None = None
        self._journal_database_path = journal_database_path
        self._monitor_database_path = monitor_database_path
        self._journal_process_manager = AuxiliaryProcessManager()
        self._journal_command_path = journal_database_path.with_name("journal_command.json") if journal_database_path else None
        self._journal_command_channel = JsonCommandChannel(self._journal_command_path, time_based_ids=True)
        self._journal_process_path = journal_database_path.with_name("journal_process.pid") if journal_database_path else None
        self._journal_news_request_path = journal_database_path.with_name("journal_news_request.json") if journal_database_path else None
        self._journal_news_inbox = JsonRequestInbox(self._journal_news_request_path)
        self._journal_background_sync_day: date | None = None
        self._entry_snapshot_writer: EntrySnapshotWriter | None = None
        self._investor_backfill_days: set[date] = set()
        if journal_database_path is not None:
            self._entry_snapshot_writer = EntrySnapshotWriter(
                journal_database_path, news_database_path, entry_investor_loader, program_trade_loader,
            )
            self._entry_snapshot_writer.failed.connect(lambda message: logger.warning("%s", message))
            self._entry_snapshot_writer.start()
        # 이전 메인 세션이 남긴 매매일지를 재사용하면 새 코드가 반영되지 않고
        # 메인 종료 연동도 끊긴다. 시작할 때 정확한 PID만 정리해 이번 세션에서 새로 띄운다.
        self._stop_stale_journal_process()
        self._news_dock_timer = QTimer(self)
        self._news_dock_timer.setSingleShot(True)
        self._news_dock_timer.setInterval(60)
        self._news_dock_timer.timeout.connect(self._sync_news_window)
        self._journal_news_timer = QTimer(self)
        self._journal_news_timer.setInterval(150)
        self._journal_news_timer.timeout.connect(self._poll_journal_news_request)
        self._journal_news_timer.start()
        # 복원 직후 ActivationChange가 command 파일의 restore를 sync로
        # 덮어쓰지 않게, 뉴스 프로세스의 80ms 폴링보다 충분히 늦게 보낸다.
        self._news_restore_sync_pending = False
        self._news_restore_sync_timer = QTimer(self)
        self._news_restore_sync_timer.setSingleShot(True)
        self._news_restore_sync_timer.setInterval(250)
        self._news_restore_sync_timer.timeout.connect(self._finish_news_restore_sync)
        self._api_reloading = False
        self._google_drive_worker_controller = GoogleDriveWorkerController(self)
        self._google_drive_worker_controller.completed.connect(
            self._on_google_drive_sync_completed
        )
        self._google_drive_worker_controller.metadata_received.connect(
            self._on_google_drive_metadata_received
        )
        self._google_drive_worker_controller.failed.connect(
            self._on_google_drive_sync_failed
        )
        self._update_worker_controller = UpdateWorkerController(self)
        self._update_worker_controller.check_completed.connect(
            self._on_update_check_completed
        )
        self._update_worker_controller.check_failed.connect(
            self._on_update_check_failed
        )
        self._update_worker_controller.download_progress.connect(
            self._on_update_download_progress
        )
        self._update_worker_controller.download_completed.connect(
            self._apply_downloaded_updates
        )
        self._update_worker_controller.download_failed.connect(
            self._on_update_download_failed
        )
        self._update_worker_controller.download_finished.connect(
            self._close_update_progress
        )
        self._google_drive_operation = ""
        self._google_drive_show_completion = False
        self._google_drive_close_pending = False
        self._google_drive_dirty = self._has_newer_local_google_drive_changes()
        self._google_drive_first_backup_pending = False
        # Drive 수정 시각 확인/다운로드는 순위 표 표시를 막지 않는다. 시작 시에는
        # 항상 로컬 데이터로 즉시 순위를 조회하고, Drive 결과는 완료되는 즉시 반영한다.
        self._initial_ranking_waits_for_google_drive = False
        self._google_drive_debounce = QTimer(self)
        self._google_drive_debounce.setSingleShot(True)
        self._google_drive_debounce.setInterval(1_500)
        self._google_drive_debounce.timeout.connect(lambda: self._start_google_drive_sync("upload"))
        # 사용자가 선택한 데이터 원본과 현재 실제 사용 중인 경로는 다를 수
        # 있다. 시놀로지 장애 시에는 설정을 바꾸지 않고 현재 경로만 로컬로
        # 표시하며, 복구되면 다시 시놀로지로 표시한다.
        self._active_api_route = ""
        self._latest_market_state: dict[str, object] = {}
        self._realtime_diagnostics: dict[str, object] = {
            "abnormal_disconnects": 0, "reconnects": 0, "last_disconnect_reason": "",
        }
        self._after_close_finalization_codes: set[str] = set()
        self._after_close_finalization_date: date | None = None
        self._after_close_minute_received: set[str] = set()
        self._after_close_daily_received: set[str] = set()
        self._finalization_attempts: dict[tuple[date, str], int] = {}
        self._finalization_retry_after: dict[tuple[date, str], datetime] = {}
        self._ranking_worker_controller = RankingWorkerController(self)
        self._ranking_worker_controller.completed.connect(self._on_ranking_loaded)
        self._ranking_worker_controller.failed.connect(self._on_ranking_failed)
        self._ranking_worker_controller.finished.connect(self._on_ranking_worker_finished)
        self._settings_dialog: SettingsDialog | None = None
        self._deferred_ranking_flush_scheduled = False
        self._table_update_deferred = False
        self._table_update_flush_scheduled = False
        self._initial_ranking_size_adjusted = False
        self._ranking_execution = RankingExecutionCoordinator(self._ranking_now)
        self._realtime_subscription = RealtimeSubscriptionCoordinator(self._ranking_now)
        self._rank_changed_codes: set[str] = set()
        self._selected_table_cell: tuple[int, int] | None = None
        self._selected_table_code: str | None = None
        self._syncing_row_heights = False
        self._row_height_dragging = False
        self._row_height_drag_start_y = 0
        self._row_height_drag_start_height = 0
        self._initial_new_high_refresh_started = False
        self._initial_nxt_codes: tuple[str, ...] = ()
        self._secondary_data_coordinator = SecondaryDataFollowupCoordinator(
            start_minute_history=lambda codes, force: self._start_minute_history_loading(codes, force=force),
            start_daily_high=lambda codes, force: self._start_daily_high_loading(codes, force=force),
            start_daily_high_phase=self._start_daily_high_phase,
            start_fundamentals=self._start_fundamentals_loading,
            start_fundamentals_phase=self._start_fundamentals_phase,
            start_nxt_phase=self._start_nxt_phase,
            start_new_high_phase=self._start_initial_new_high_refresh,
        )
        self._closing = False
        self._row_by_code: dict[str, int] = {}
        self._ranked_stock_names: dict[str, str] = {}
        self._stock_markets: dict[str, str] = {}
        self._current_prices: dict[str, int] = {}
        self._last_change_rates: dict[str, float] = {}
        self._realtime_pressure: dict[str, deque[tuple[datetime, int]]] = {}
        self._latest_execution_strength: dict[str, float] = {}
        self._latest_session_type: dict[str, str] = {}
        self._latest_program_trade: dict[str, dict[str, object]] = {}
        # 0B FID 311 수신값. 장중 표 시가총액에는 ka10001 캐시보다 우선한다.
        self._realtime_market_caps: dict[str, float] = {}
        self._today_high_codes: set[str] = set()
        self._today_high_prices: dict[str, int] = {}
        self._near_high_codes: set[str] = set()
        self._near_high_levels: dict[str, str] = {}
        self._near_high_sound_last_played: dict[tuple[str, str], float] = {}
        self._nxt_checked_codes: set[str] = set()
        self._nxt_enabled_codes: set[str] = set()
        self._near_high_sound_players: dict[str, tuple[QMediaPlayer, QAudioOutput]] = {}
        self._new_high_periods: dict[str, frozenset[int]] = {}
        self._minute_history_codes: set[str] = set()
        self._minute_aggregator = minute_aggregator or MinuteTradeValueAggregator()
        self._minute_history_worker_controller = MinuteHistoryWorkerController(
            self, minute_history_worker_factory
        )
        self._minute_history_worker_controller.history_received.connect(
            self._on_history_received
        )
        self._minute_history_worker_controller.status_changed.connect(
            self.statusBar().showMessage
        )
        self._minute_history_worker_controller.failed.connect(
            self._on_background_failure
        )
        self._minute_history_worker_controller.finished.connect(
            lambda codes, forced: self._secondary_data_coordinator.minute_history_finished(
                codes, forced=forced
            )
        )
        self._daily_high_worker_controller = DailyHighWorkerController(
            self, daily_high_worker_factory
        )
        self._daily_high_worker_controller.received.connect(
            self._on_daily_high_received
        )
        self._daily_high_worker_controller.failed.connect(
            self._on_background_failure
        )
        self._daily_high_worker_controller.finished.connect(
            self._on_daily_high_worker_finished
        )
        self._fundamentals_worker_controller = FundamentalsWorkerController(
            self, fundamentals_worker_factory
        )
        self._fundamentals_worker_controller.received.connect(
            self._on_fundamentals_received
        )
        self._fundamentals_worker_controller.failed.connect(
            self._on_background_failure
        )
        self._fundamentals_worker_controller.completed.connect(
            self._on_fundamentals_completed
        )
        self._historical_high_worker_controller = HistoricalHighWorkerController(
            self, historical_high_worker_factory
        )
        self._historical_high_worker_controller.received.connect(
            self._on_historical_high_received
        )
        self._historical_high_worker_controller.failed.connect(
            self._on_background_failure
        )
        self._historical_high_worker_controller.finished.connect(
            self._on_historical_high_worker_finished
        )
        self._nxt_eligibility_worker_controller = NxtEligibilityWorkerController(
            self, nxt_eligibility_worker_factory
        )
        self._nxt_eligibility_worker_controller.received.connect(
            self._on_nxt_eligibility_received
        )
        self._nxt_eligibility_worker_controller.failed.connect(
            self._on_background_failure
        )
        self._nxt_eligibility_worker_controller.finished.connect(
            self._start_daily_krx_catalog_sync
        )
        self._nxt_eligibility_worker_controller.finished.connect(
            lambda: self._start_realtime_subscription(tuple(self._row_by_code))
        )
        self._nxt_eligibility_worker_controller.finished.connect(
            lambda: self._start_realtime_followups(tuple(self._row_by_code))
        )
        self._new_high_worker_controller = NewHighWorkerController(self)
        self._new_high_worker_controller.completed.connect(
            self._on_new_high_refresh_completed
        )
        self._new_high_worker_controller.failed.connect(
            self._on_new_high_refresh_failed
        )
        self._new_high_worker_controller.finished.connect(
            lambda: self._new_high_button.setEnabled(True)
        )
        self._krx_stock_catalog_worker_controller = KrxStockCatalogWorkerController(self)
        self._image_theme_ocr_worker_controller = ImageThemeOcrWorkerController(self)
        self._image_theme_ocr_worker_controller.progress.connect(
            self._on_image_theme_ocr_progress
        )
        self._image_theme_ocr_worker_controller.completed.connect(
            lambda _rows: self._close_image_theme_ocr_progress()
        )
        self._image_theme_ocr_worker_controller.completed.connect(
            self._show_image_theme_rows
        )
        self._image_theme_ocr_worker_controller.failed.connect(
            self._on_image_theme_ocr_failed
        )
        self._image_theme_ocr_worker_controller.finished.connect(
            self._close_image_theme_ocr_progress
        )
        self._image_theme_ocr_worker_controller.finished.connect(
            self._on_image_theme_ocr_finished
        )
        self._image_theme_ocr_worker_controller.finished.connect(
            lambda: self.statusBar().showMessage("이미지 OCR 작업 종료")
        )
        self._realtime_worker_controller = RealtimeWorkerController(
            self, realtime_worker_factory
        )
        self._realtime_worker_controller.trade_received.connect(self._on_trade_tick)
        self._realtime_worker_controller.order_executed.connect(self._on_order_execution)
        self._realtime_worker_controller.market_state_received.connect(self._on_market_index_tick)
        self._realtime_worker_controller.program_trade_received.connect(self._on_program_trade_tick)
        self._realtime_worker_controller.diagnostics_changed.connect(self._on_realtime_diagnostics_changed)
        self._realtime_worker_controller.status_changed.connect(self._on_realtime_status_changed)
        self._realtime_worker_controller.connection_failed.connect(self._on_realtime_failure)
        self._realtime_worker_controller.connection_opened.connect(
            lambda codes: self._minute_aggregator.reset_cumulative_baselines(codes)
        )
        self._realtime_worker_controller.codes_added.connect(
            lambda codes: self._minute_aggregator.reset_cumulative_baselines(codes)
        )
        self._realtime_worker_controller.subscription_ready.connect(
            self._start_realtime_followups
        )
        # 코호트·기준값·분 마감은 화면과 독립된 수집 객체가 소유한다.
        self._top20_collector = Top20TradeValueCollector()
        self._top20_view_date = date.today()
        self._top20_view_mode = "minute"
        self._top20_repair_worker_controller = Top20MarketRepairWorkerController(self)
        self._top20_repair_worker_controller.completed.connect(
            self._on_top20_market_repaired
        )
        self._top20_repair_worker_controller.failed.connect(
            lambda message: logger.warning("TOP20 시장 구분 재확인 실패: %s", message)
        )
        self._top20_repair_last_started = 0.0
        self._minute_bar_repository = minute_bar_repository
        if self._minute_bar_repository is not None:
            try:
                self._minute_bar_repository.repair_top20_market_splits()
                saved_index = self._minute_bar_repository.load_recent_top20_trade_value_index(
                    1_440, trade_date=date.today(),
                )
                self._top20_collector.completed.extend(
                    (minute, kospi, kosdaq, unknown)
                    for minute, _, _, _, kospi, kosdaq, unknown in saved_index
                )
            except Exception as error:
                logger.warning("TOP20 거래대금 지수 저장값 로드 실패: %s", error)
        self._daily_bar_repository = daily_bar_repository
        self._minute_bar_storage_date: date | None = None
        self._pending_minute_bars: dict[tuple[str, datetime], MinuteOhlcv] = {}
        self._pending_market_index_bars: dict[
            tuple[str, datetime], tuple[float, float, float, float, float | None]
        ] = {}
        self._minute_bar_save_timer = QTimer(self)
        self._minute_bar_save_timer.setSingleShot(True)
        self._minute_bar_save_timer.setInterval(1_000)
        self._minute_bar_save_timer.timeout.connect(self._flush_pending_minute_bars)
        self._pending_daily_trade_comparisons: dict[str, tuple[tuple[str, float], ...]] = {}
        self._daily_trade_comparison_timer = QTimer(self)
        self._daily_trade_comparison_timer.setSingleShot(True)
        self._daily_trade_comparison_timer.setInterval(500)
        self._daily_trade_comparison_timer.timeout.connect(self._flush_daily_trade_comparisons)
        self._ranking_timer = QTimer(self)
        self._ranking_timer.setSingleShot(True)
        self._ranking_timer.timeout.connect(self._on_ranking_timer)
        self._rank_changed_highlight_timer = QTimer(self)
        self._rank_changed_highlight_timer.setSingleShot(True)
        self._rank_changed_highlight_timer.timeout.connect(self._clear_rank_changed_highlights)
        self._realtime_session_timer = QTimer(self)
        self._realtime_session_timer.setSingleShot(True)
        self._realtime_session_timer.timeout.connect(self._on_realtime_session_boundary)
        self._ranking_preparation_timer = QTimer(self)
        self._ranking_preparation_timer.setSingleShot(True)
        self._ranking_preparation_timer.timeout.connect(self._prepare_ranking_refresh)
        self._clock_label = QLabel()
        self.statusBar().addPermanentWidget(self._clock_label)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock_label)
        self._clock_timer.start(1000)
        self._update_clock_label()
        # 일반 상태 메시지를 가리지 않도록 안내문은 일정 시간만 표시한 뒤,
        # 그 사이 다른 메시지가 없다면 직전 문구로 되돌린다.
        self._investment_notice_previous_message = ""
        self._investment_notice_timer = QTimer(self)
        self._investment_notice_timer.setInterval(15 * 60 * 1_000)
        self._investment_notice_timer.timeout.connect(self._show_investment_notice)
        self._investment_notice_timer.start()
        self._investment_notice_restore_timer = QTimer(self)
        self._investment_notice_restore_timer.setSingleShot(True)
        self._investment_notice_restore_timer.setInterval(8_000)
        self._investment_notice_restore_timer.timeout.connect(self._restore_status_after_investment_notice)
        self._window_geometry_save_timer = QTimer(self)
        self._window_geometry_save_timer.setSingleShot(True)
        self._window_geometry_save_timer.setInterval(350)
        self._window_geometry_save_timer.timeout.connect(self._save_window_geometry)
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(int(self._settings.get("window_width")), int(self._settings.get("window_height")))
        self.setMinimumWidth(320)
        self._restore_window_position()

        toolbar = QToolBar("도구")
        toolbar.setObjectName("main_tools_toolbar")
        toolbar.setMovable(False)
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # 자동 순위 갱신을 사용하므로 테스트앱 도구막대에는 수동 새로고침
        # 버튼을 표시하지 않는다. 내부 작업 상태 제어용 객체만 유지한다.
        self._refresh_button = QPushButton("새로고침", self)
        self._refresh_button.clicked.connect(self._refresh_rankings)
        self._refresh_button.setEnabled(ranking_loader is not None)
        self._refresh_button.hide()
        self._new_high_button = QPushButton("신고가 새로고침", self)
        self._new_high_button.clicked.connect(self._refresh_new_highs)
        self._new_high_button.setEnabled(ranking_loader is not None)
        self._new_high_button.hide()
        settings_button = QPushButton("⚙")
        settings_button.setObjectName("main_settings_button")
        settings_button.setToolTip("기본 설정")
        settings_button.setAccessibleName("기본 설정")
        settings_button.clicked.connect(self._open_settings)
        top20_index_button = QPushButton("TOP20 지수")
        top20_index_button.setObjectName("top20_index_button")
        top20_index_button.setToolTip("각 순위의 20종목을 다음 30초에 적용하고 두 구간을 1분으로 합산합니다.")
        top20_index_button.clicked.connect(self._show_top20_trade_value_window)
        toolbar.addWidget(top20_index_button)
        journal_button = QPushButton("매매일지")
        journal_button.setToolTip("과거 매매목록을 열고, 종목이 선택되어 있으면 오늘 분봉도 함께 표시합니다.")
        journal_button.clicked.connect(self._show_trading_journal)
        toolbar.addWidget(journal_button)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self._rank_query_selector = QComboBox()
        for label, value in (("30초 간격", "5"), ("1분 간격", "1"), ("10분 간격", "2"), ("1시간 간격", "3"), ("당일 누적", "4")):
            self._rank_query_selector.addItem(label, value)
        saved_rank_query = self._settings.get("rank_query_type")
        self._rank_query_selector.setCurrentIndex(("5", "1", "2", "3", "4").index(saved_rank_query) if saved_rank_query in {"1", "2", "3", "4", "5"} else 0)
        self._rank_query_selector.currentIndexChanged.connect(self._change_rank_query_type)
        toolbar.addWidget(self._rank_query_selector)
        self._api_status = QLabel("API: 대기")
        toolbar.addWidget(self._api_status)
        version_label = QLabel(f"버전 {APP_VERSION}")
        version_label.setObjectName("main_version_label")
        version_label.setStyleSheet("color: #667085; padding-left: 8px;")
        toolbar.addWidget(version_label)
        settings_button.setFixedSize(24, 22)
        settings_button.setStyleSheet("padding: 0; margin-left: 2px;")
        toolbar.addWidget(settings_button)
        self._environment_selector = QComboBox()
        self._environment_selector.addItem("모의투자", "mock")
        self._environment_selector.addItem("실전투자", "real")
        self._restore_environment_selector()
        self._environment_selector.currentIndexChanged.connect(self._change_environment)
        # 실행 환경은 API 설정 창에서만 바꾼다. 메인에는 자주 확인하는
        # 연결 상태와 버전만 남겨 공간과 시각적 혼잡을 줄인다.
        self._environment_selector.hide()
        self.addToolBar(toolbar)

        self._table = QTableWidget(1, len(self.HEADERS))
        self._table.setHorizontalHeaderLabels(self.HEADERS)
        # 행 번호는 표시하지 않는다. 행 높이는 순위 칸 안의 가는 경계선을
        # 드래그해서 조절한다.
        vertical_header = self._table.verticalHeader()
        vertical_header.setVisible(False)
        vertical_header.setMinimumSectionSize(12)
        vertical_header.setMaximumSectionSize(100)
        vertical_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # 순위 칸의 행 경계에 올렸을 때에도 크기 조절 커서를 보여 준다.
        self._table.setMouseTracking(True)
        self._table.viewport().setMouseTracking(True)
        self._table.cellClicked.connect(self._handle_main_table_click)
        self._table.cellDoubleClicked.connect(self._handle_main_table_double_click)
        self._table.setItemDelegate(NxtMarkerDelegate(self._table))
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        # 행 전체 선택은 Qt에서 모든 열이 선택된 것으로 간주되어 모든 제목을
        # 한꺼번에 강조한다. 기본 강조는 끄고 실제 클릭한 셀의 제목만 표시한다.
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.horizontalHeader().setSectionsMovable(True)
        self._column_controller = MainTableColumnController(
            self._table, self._columns, self.COLUMNS
        )
        self._table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.horizontalHeader().customContextMenuRequested.connect(self._show_column_menu)
        self._table.horizontalHeader().sectionMoved.connect(lambda *_: self._save_columns())
        self._table.horizontalHeader().sectionResized.connect(self._on_column_resized)
        self._table.horizontalHeader().sectionClicked.connect(self._toggle_table_header_mode)
        # 표의 오른쪽/아래 빈 공간을 더블 클릭하면 창을 표 크기에 맞춘다.
        self._table.viewport().installEventFilter(self)
        self._update_trade_display_headers()
        self._update_high_display_headers()
        self._restore_columns()
        self._loading_label = QLabel("조회 중입니다…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet("color: #667085; font-size: 16px; padding: 24px;")
        table_area = QWidget()
        self._table_stack = QStackedLayout(table_area)
        self._table_stack.setContentsMargins(0, 0, 0, 0)
        self._table_stack.addWidget(self._loading_label)
        self._table_stack.addWidget(self._table)
        self._table_stack.setCurrentWidget(self._loading_label)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(table_area, 1)
        self._top20_trade_value_window = Top20TradeValueWindow(self)
        self._top20_trade_value_chart = self._top20_trade_value_window.chart
        self._top20_trade_value_window.dateRequested.connect(self._show_top20_trade_value_date)
        self._top20_trade_value_window.modeRequested.connect(self._show_top20_trade_value_mode)
        self._top20_trade_value_window.statisticsRequested.connect(self._show_top20_trade_value_statistics)
        self._top20_index_timer = QTimer(self)
        self._top20_index_timer.setInterval(250)
        self._top20_index_timer.timeout.connect(self._update_top20_trade_value_index)
        self._top20_index_timer.start()
        QTimer.singleShot(0, self._start_top20_market_repair)
        self._theme_trade_summary = ClickableLabel(self._cycle_theme_trade_summary_period, "상위 테마 거래대금: 순위 조회 후 표시됩니다.")
        self._theme_trade_summary.setStyleSheet("padding: 5px 8px; color: #333; background: #F5F7FA; border: 1px solid #D9E2F3;")
        self._theme_trade_summary.setToolTip("클릭하면 1분 · 5분 · 60분 · 1일 기준으로 전환합니다.")
        self._theme_trade_summary.setVisible(self._settings.get("theme_trade_summary_enabled") == "1")
        layout.addWidget(self._theme_trade_summary)
        self._theme_trade_excluded_summary = ClickableLabel(self._cycle_theme_trade_summary_period)
        self._theme_trade_excluded_summary.setStyleSheet("padding: 5px 8px; color: #333; background: #F8F3FF; border: 1px solid #D8C8EE;")
        self._theme_trade_excluded_summary.setToolTip("클릭하면 1분 · 5분 · 60분 · 1일 기준으로 전환합니다.")
        self._theme_trade_excluded_summary.setVisible(False)
        layout.addWidget(self._theme_trade_excluded_summary)
        self._theme_trade_summary_timer = QTimer(self)
        self._theme_trade_summary_timer.setSingleShot(True)
        self._theme_trade_summary_timer.setInterval(200)
        self._theme_trade_summary_timer.timeout.connect(self._refresh_theme_trade_summary)
        self._price_cache_timer = QTimer(self)
        self._price_cache_timer.setSingleShot(True)
        self._price_cache_timer.setInterval(1_000)
        self._price_cache_timer.timeout.connect(self._save_current_price_cache)
        self.setCentralWidget(content)
        self._apply_table_visuals()
        message = "키움 REST 순위를 자동으로 조회합니다." if ranking_loader else "상단 API 설정에서 키를 입력해 연결할 수 있습니다."
        self.statusBar().showMessage(message)
        if ranking_loader is not None:
            if initial_google_drive_download:
                self._start_initial_ranking()
                QTimer.singleShot(0, lambda: self._start_google_drive_sync("metadata"))
            else:
                self._start_initial_ranking()
                QTimer.singleShot(0, self._resume_pending_google_drive_upload)
        else:
            QTimer.singleShot(0, self._open_api_settings)
        # 앱을 먼저 표시한 뒤에만 백그라운드 업데이트 확인을 시작한다.
        # 개발 실행은 GitHub 릴리즈를 갱신하지 않으므로 설치본에서만 동작한다.
        if getattr(sys, "frozen", False) and self._settings.get("auto_update_check") == "1":
            QTimer.singleShot(1_200, lambda: self._check_for_updates(silent=True))

    def _show_investment_notice(self) -> None:
        """상태 표시줄에 투자 유의 안내를 잠시 보여 준다."""
        if self._closing:
            return
        self._investment_notice_previous_message = self.statusBar().currentMessage()
        self.statusBar().showMessage(INVESTMENT_NOTICE)
        self._investment_notice_restore_timer.start()

    def _restore_status_after_investment_notice(self) -> None:
        """안내 표시 중 새 상태가 없을 때만 이전 문구를 복원한다."""
        if self.statusBar().currentMessage() == INVESTMENT_NOTICE:
            self.statusBar().showMessage(self._investment_notice_previous_message)

    def _open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(
            self._settings,
            self._api_config_path(),
            self._open_log_file,
            self._open_theme_manager,
            self,
            self._open_column_manager,
            self._export_settings_backup,
            self._import_settings_backup,
            lambda parent: ThemeManagerDialog(self._theme_store, self._settings, self._select_excel, self._select_theme_image, self._sync_krx_stock_catalog, parent, self._on_themes_changed) if self._theme_store is not None else QWidget(parent),
            column_manager_panel_factory=lambda parent: ColumnManagerDialog(self._columns, self.COLUMNS, self._table, parent, embedded=True, on_applied=self._apply_column_settings) if self._columns is not None else QWidget(parent),
            stock_lookup=self._stock_lookup,
            drive_connector=self._connect_google_drive,
            drive_downloader=lambda: self._start_google_drive_sync("download", notify_on_success=True),
            drive_uploader=lambda: self._start_google_drive_sync("upload", notify_on_success=True),
            drive_disconnector=self._disconnect_google_drive,
            drive_status=self._google_drive_status,
            drive_client_importer=self._select_google_drive_client,
            theme_backup_exporter=self._export_theme_backup,
            theme_backup_importer=self._import_theme_backup,
            update_checker=self._check_for_updates,
            journal_backup_exporter=self._export_journal_backup,
            journal_backup_importer=self._import_journal_backup,
            news_api_settings_opener=self._open_news_api_settings,
        )
        # 기본 설정은 메인 표를 막지 않는 별도 창으로 연다. 따라서 순위 갱신은
        # 설정 창이 열려 있어도 즉시 표에 반영된다.
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.finished.connect(lambda result, source=dialog: self._on_settings_closed(source, result))
        self._settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_news_api_settings(self) -> None:
        """기본설정에서 뉴스·공시·AI 키를 한곳에서 편집한다."""
        if self._news_api_settings_dialog is not None and self._news_api_settings_dialog.isVisible():
            self._news_api_settings_dialog.raise_()
            self._news_api_settings_dialog.activateWindow()
            return
        if self._news_config_path is None:
            self.statusBar().showMessage("뉴스 설정 파일 위치를 찾지 못했습니다.")
            return
        from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig
        from kiwoom_monitor.presentation.stock_news_window import NaverNewsSettingsDialog

        try:
            source = DataSourceConfig(self._news_config_path.with_name("data_source.json")).load()
            operational_client = (
                CentralOperationalSettingsClient(source)
                if source.mode in {"local_server", "personal_server"} else None
            )
            dialog = NaverNewsSettingsDialog(
                LocalNaverNewsConfig(self._news_config_path),
                self,
                database_path=self._news_database_path,
                section="connections",
                operational_client=operational_client,
            )
        except (OSError, ValueError):
            QMessageBox.warning(self, "뉴스 API 설정", "저장된 뉴스 API 설정을 읽지 못했습니다. 설정 파일을 다시 만들어 주세요.")
            return
        self._news_api_settings_dialog = dialog
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.accepted.connect(self._on_news_api_settings_saved)
        dialog.finished.connect(lambda _result, source=dialog: self._clear_news_api_settings_dialog(source))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_news_api_settings_dialog(self, dialog: QDialog) -> None:
        if self._news_api_settings_dialog is dialog:
            self._news_api_settings_dialog = None

    def _on_news_api_settings_saved(self) -> None:
        if self._news_process_manager.is_running:
            self._send_news_command(action="reload_settings", activate=False)
        self.statusBar().showMessage("뉴스·DART·AI 설정을 저장했습니다.")

    def _check_for_updates(self, silent: bool = False) -> None:
        if self._update_worker_controller.is_check_running:
            self.statusBar().showMessage("업데이트 정보를 확인하고 있습니다…")
            return
        self.statusBar().showMessage("업데이트 정보를 확인하고 있습니다…")
        self._update_worker_controller.start_check(silent=silent)

    @staticmethod
    def _format_update_size(size: int) -> str:
        return f"{size / 1024 / 1024:.1f}MB"

    def _on_update_check_completed(self, plan: UpdatePlan | None, silent: bool = False) -> None:
        if plan is None:
            self.statusBar().showMessage("현재 최신 버전을 사용하고 있습니다.", 4_000)
            if not silent:
                QMessageBox.information(self, "앱 업데이트", f"현재 최신 버전입니다.\n\n현재 버전: {APP_VERSION}")
            return
        if not plan.can_apply_steps:
            answer = QMessageBox.question(
                self,
                "앱 업데이트",
                f"새 버전 {plan.latest_version}이 있습니다.\n현재 버전: {APP_VERSION}\n\n중간 버전용 검증된 부분 업데이트 파일이 부족합니다. 전체 설치 파일을 받으세요. 릴리즈 페이지를 열까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(plan.release_url))
            return
        if not getattr(sys, "frozen", False):
            QMessageBox.information(self, "앱 업데이트", "개발 실행 중에는 자동 업데이트를 적용하지 않습니다.\n설치본에서 자동 업데이트를 사용할 수 있습니다.")
            return
        if plan.should_use_setup:
            answer = QMessageBox.question(
                self,
                "앱 업데이트",
                f"새 버전 {plan.latest_version}이 있습니다.\n현재 버전: {APP_VERSION}\n\n"
                f"부분 업데이트 {len(plan.steps)}개 합계: {self._format_update_size(plan.update_size)}\n"
                f"전체 설치 파일: {self._format_update_size(plan.setup_size)}\n\n"
                "전체 설치 파일이 더 작거나 같습니다. 릴리즈 페이지를 열어 설치 파일을 받을까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(plan.release_url))
            return
        sequence = " → ".join(step.version for step in plan.steps)
        answer = QMessageBox.question(
            self,
            "앱 업데이트",
            f"새 버전 {plan.latest_version}이 있습니다.\n현재 버전: {APP_VERSION}\n\n"
            f"부분 업데이트 순서: {sequence}\n"
            f"다운로드 합계: {self._format_update_size(plan.update_size)}\n\n"
            "변경된 파일만 순서대로 다운로드해 설치할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._download_updates(plan.steps)

    def _download_updates(self, steps: tuple[UpdateStep, ...]) -> None:
        if self._update_worker_controller.is_download_running:
            self.statusBar().showMessage("업데이트 파일을 이미 다운로드하고 있습니다…")
            return
        progress = QProgressDialog("업데이트 파일을 다운로드하고 있습니다…", "취소", 0, 100, self)
        progress.setWindowTitle("앱 업데이트")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._update_progress_dialog = progress
        progress.canceled.connect(self._update_worker_controller.request_download_interruption)
        self.statusBar().showMessage("업데이트 파일을 다운로드하고 있습니다…")
        progress.show()
        if not self._update_worker_controller.start_download(steps):
            self._close_update_progress()

    def _on_update_download_progress(self, value: int) -> None:
        progress = getattr(self, "_update_progress_dialog", None)
        if progress is not None:
            progress.setValue(value)

    def _close_update_progress(self) -> None:
        if getattr(self, "_update_progress_dialog", None) is not None:
            self._update_progress_dialog.close()
            self._update_progress_dialog = None

    def _apply_downloaded_updates(self, archive_paths: object) -> None:
        if not isinstance(archive_paths, (tuple, list)) or not archive_paths:
            QMessageBox.critical(self, "앱 업데이트", "다운로드한 업데이트 파일을 찾을 수 없습니다.")
            return
        app_root = Path(sys.executable).resolve().parent
        updates_dir = AppPaths.for_current_user().data_dir.parent / "updates"
        bundled_helper = app_root / "_internal" / "update_helper" / "UpdateHelper.exe"
        helper = updates_dir / "UpdateHelper.exe"
        log_path = updates_dir / "apply_update.log"
        try:
            if not bundled_helper.is_file():
                raise OSError("업데이트 도우미를 찾을 수 없습니다. 설치 파일로 다시 설치하세요.")
            updates_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled_helper, helper)
            arguments_list = [
                "--wait-pid", str(os.getpid()),
                "--target", str(app_root),
                "--exe", str(app_root / Path(sys.executable).name),
                "--log", str(log_path),
            ]
            for archive_path in archive_paths:
                arguments_list.extend(("--archive", str(archive_path)))
            arguments = subprocess.list2cmdline(arguments_list)
            result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(helper), arguments, None, 0)
            if result <= 32:
                raise OSError(f"Windows 권한 확인이 취소되었거나 시작에 실패했습니다. ({result})")
        except OSError as error:
            QMessageBox.critical(self, "앱 업데이트", f"업데이트 설치를 시작하지 못했습니다.\n{error}")
            return
        self.statusBar().showMessage("업데이트 설치를 시작합니다. Windows 권한 확인 후 앱이 다시 열립니다.")
        QTimer.singleShot(300, self.close)

    def _on_update_download_failed(self, message: str) -> None:
        logger.warning("업데이트 다운로드 실패: %s", message)
        if "검증" in message:
            QMessageBox.warning(self, "앱 업데이트", "업데이트 파일 검증에 실패해 적용하지 않았습니다. 잠시 후 다시 시도하세요.")
            return
        QMessageBox.information(self, "앱 업데이트", "업데이트 파일을 내려받지 못했습니다. 잠시 후 다시 시도하세요.")

    def _on_update_check_failed(self, message: str, silent: bool = False) -> None:
        logger.info("업데이트 확인 실패: %s", message)
        self.statusBar().showMessage("업데이트 정보를 확인하지 못했습니다.", 5_000)
        if not silent:
            QMessageBox.information(self, "앱 업데이트", "업데이트 정보를 확인하지 못했습니다.\n네트워크 연결 또는 GitHub 릴리즈 공개 상태를 확인하세요.")

    def _on_settings_closed(self, dialog: SettingsDialog, result: int) -> None:
        if self._settings_dialog is dialog:
            self._settings_dialog = None
        if result != QDialog.DialogCode.Accepted:
            return
        if self._ranking_loader is not None and hasattr(self._ranking_loader, "set_query_type"):
            self._ranking_loader.set_query_type(self._settings.get("rank_query_type"))
        saved_rank_query = self._settings.get("rank_query_type")
        self._rank_query_selector.setCurrentIndex(("5", "1", "2", "3", "4").index(saved_rank_query) if saved_rank_query in {"1", "2", "3", "4", "5"} else 0)
        self._schedule_next_ranking_refresh()
        self._apply_table_visuals()
        self._update_clock_label()
        self._theme_trade_summary.setVisible(self._settings.get("theme_trade_summary_enabled") == "1")
        for code in self._row_by_code:
            self._apply_near_high_background(code)
            current_price = self._current_prices.get(code)
            if current_price is not None:
                self._set_near_high_level(code, current_price, play_sound=False)
                self._apply_near_high_background(code)
            self._render_new_high_price(code)
            self._render_high_distance(code)
            self._render_trade_values(code)
            self._render_market_cap(code)
        if bool(getattr(self, "_theme_group_sort_enabled", False)):
            self._sort_visible_rows_by_theme_group(True)
        self.statusBar().showMessage("기본 설정 저장 완료")
        self._schedule_google_drive_upload()
        if dialog.api_changed:
            if self._news_process_manager.is_running:
                self._send_news_command(action="reload_settings", activate=False)
            self._restart_for_api_settings()

    def _on_themes_changed(self) -> None:
        if self._theme_store is not None:
            self._themes = self._theme_store.all_by_name()
        self._refresh_rankings()
        self._schedule_google_drive_upload("both")

    def _start_initial_ranking(self) -> None:
        if self._ranking_loader is None:
            return
        self._initial_ranking_waits_for_google_drive = False
        QTimer.singleShot(0, self._refresh_rankings)
        self._schedule_next_ranking_refresh()
        self._schedule_realtime_session_refresh()

    def _google_drive_status(self) -> str:
        if self._google_drive_sync is None:
            return "이 기능을 사용할 수 없습니다."
        if self._google_drive_sync.connected:
            automatic: list[str] = []
            if self._settings.get("google_drive_auto_download") == "1":
                automatic.append("시작 시 다운로드")
            if self._settings.get("google_drive_auto_upload") == "1":
                automatic.append("변경 후 업로드")
            if self._settings.get("google_drive_auto_upload_on_exit") == "1":
                automatic.append("종료 시 업로드")
            return "연결됨 · " + (", ".join(automatic) if automatic else "수동 동기화")
        if self._google_drive_sync.configured:
            return "연결되지 않음 · Google Drive 연결을 누르면 로그인합니다."
        return "OAuth JSON을 연결한 뒤 Google Drive 연결을 누르면 로그인합니다."

    def _select_google_drive_client(self) -> None:
        if self._google_drive_sync is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Google OAuth JSON 선택",
            "",
            "Google OAuth JSON (*.json)",
        )
        if not path:
            return
        try:
            self._google_drive_sync.import_client_file(Path(path))
        except GoogleDriveSyncError as error:
            QMessageBox.warning(self, "OAuth JSON 연결", str(error))
            return
        self._refresh_google_drive_status()
        QMessageBox.information(
            self,
            "OAuth JSON 연결",
            "이 컴퓨터에 개인 OAuth 구성을 저장했습니다.\n이제 Google Drive 연결을 눌러 본인 계정으로 로그인하세요.",
        )

    def _connect_google_drive(self) -> None:
        if self._google_drive_sync is None or not self._google_drive_sync.configured:
            QMessageBox.warning(self, "Google Drive 연결", "Google Drive 연결 구성이 아직 포함되지 않았습니다. 프로그램을 다시 설치하거나 관리자에게 문의하세요.")
            return
        self._start_google_drive_sync("metadata", interactive=True, allow_connect=True)

    def _disconnect_google_drive(self) -> None:
        if self._google_drive_sync is None:
            return
        self._google_drive_sync.disconnect()
        self._refresh_google_drive_status()
        self.statusBar().showMessage("Google Drive 계정 연결을 해제했습니다. 기존 Drive 파일은 그대로 유지됩니다.")

    def _schedule_google_drive_upload(self, target_override: str = "") -> None:
        if self._google_drive_sync is None or self._closing:
            return
        if target_override == "both":
            self._google_drive_pending_target = "both"
        self._google_drive_dirty = True
        self._settings.set("google_drive_unsynced_changes", "1")
        self._settings.set("google_drive_local_changed_at", self._google_drive_timestamp_now())
        if self._google_drive_sync.connected and self._settings.get("google_drive_auto_upload") == "1":
            self._google_drive_debounce.start()

    def _has_newer_local_google_drive_changes(self) -> bool:
        local_changed_at = self._settings.get("google_drive_local_changed_at")
        last_upload_at = self._settings.get("google_drive_last_upload_success_at")
        return self._settings.get("google_drive_unsynced_changes") == "1" or (
            bool(local_changed_at) and (not last_upload_at or local_changed_at > last_upload_at)
        )

    def _resume_pending_google_drive_upload(self) -> None:
        """이전 업로드 실패분만 비동기로 다시 보낸다. 시작 다운로드는 하지 않는다."""
        if (
            self._google_drive_dirty
            and self._google_drive_sync is not None
            and self._google_drive_sync.connected
            and self._settings.get("google_drive_auto_upload") == "1"
            and not self._closing
        ):
            self.statusBar().showMessage("이전 Google Drive 업로드 실패분을 다시 업로드합니다…")
            self._start_google_drive_sync("upload")

    def _start_google_drive_sync(self, operation: str, interactive: bool = False, close_after: bool = False, allow_connect: bool = False, notify_on_success: bool = False) -> None:
        service = self._google_drive_sync
        if service is None or not service.configured:
            return
        if not service.connected and not allow_connect:
            self.statusBar().showMessage("Google Drive를 먼저 연결하세요.")
            return
        if self._google_drive_worker_controller.is_running:
            if close_after:
                self._google_drive_close_pending = True
            return
        self._google_drive_operation = operation
        self._google_drive_show_completion = notify_on_success
        target = self._settings.get("google_drive_sync_target")
        if operation == "upload" and (self._google_drive_pending_target or self._has_newer_local_google_drive_changes()):
            # 미동기 변경은 설정과 최신 테마 프로필을 한 세대로 함께 올린다.
            target = "both"
        if not self._google_drive_worker_controller.start(
            service,
            operation,
            target,
            interactive,
        ):
            self._google_drive_operation = ""
            self._google_drive_show_completion = False
            return
        self._google_drive_active_target = target if operation == "upload" else ""
        if operation == "upload":
            self._google_drive_pending_target = ""
        if close_after:
            self._google_drive_close_pending = True
        self.statusBar().showMessage("Google Drive 동기화 중…")

    @staticmethod
    def _parse_google_drive_time(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _google_drive_timestamp_now() -> str:
        return datetime.now().astimezone().astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _on_google_drive_metadata_received(self, remote_modified_at: str) -> None:
        """시작 자동 동기화 전에 Drive와 로컬의 최신 변경을 안전하게 비교한다."""
        self._google_drive_operation = ""
        self._google_drive_pending_target = ""
        self._google_drive_active_target = ""
        self._refresh_google_drive_status()
        local_changed_at = self._settings.get("google_drive_local_changed_at")
        last_upload_at = self._settings.get("google_drive_last_upload_success_at")
        local_dirty = self._has_newer_local_google_drive_changes()
        remote_time = self._parse_google_drive_time(remote_modified_at)
        last_upload_time = self._parse_google_drive_time(last_upload_at)

        if remote_time is None:
            # 최초 연결과 기존 단일 백업 호환 처리는 download()가 담당한다.
            self._start_google_drive_sync("download")
            return
        if local_dirty:
            remote_is_newer = last_upload_time is None or remote_time > last_upload_time
            if remote_is_newer:
                choice = QMessageBox(self)
                choice.setWindowTitle("Google Drive 동기화 충돌")
                choice.setIcon(QMessageBox.Icon.Warning)
                choice.setText("이 컴퓨터와 Google Drive에 모두 더 최근 변경이 있습니다.")
                choice.setInformativeText("자동으로 덮어쓰지 않았습니다. 사용할 데이터를 선택하세요.")
                upload = choice.addButton("로컬 업로드", QMessageBox.ButtonRole.AcceptRole)
                download = choice.addButton("Drive 다운로드", QMessageBox.ButtonRole.DestructiveRole)
                cancel = choice.addButton("이번에는 로컬 유지", QMessageBox.ButtonRole.RejectRole)
                choice.exec()
                if choice.clickedButton() is upload:
                    self._finish_initial_drive_check()
                    self._start_google_drive_sync("upload")
                elif choice.clickedButton() is download:
                    self._start_google_drive_sync("download")
                else:
                    self.statusBar().showMessage("Google Drive 충돌: 이번 실행은 로컬 데이터를 유지합니다.")
                    self._finish_initial_drive_check()
                return
            if self._settings.get("google_drive_auto_upload") == "1":
                self._finish_initial_drive_check()
                self._start_google_drive_sync("upload")
            else:
                self.statusBar().showMessage("로컬 변경이 있어 Google Drive 자동 다운로드를 건너뛰었습니다.")
                self._finish_initial_drive_check()
            return

        if last_upload_time is None or remote_time > last_upload_time:
            self._start_google_drive_sync("download")
            return
        self._finish_initial_drive_check()

    def _finish_initial_drive_check(self) -> None:
        if self._initial_ranking_waits_for_google_drive:
            self._start_initial_ranking()

    def _on_google_drive_sync_completed(self, message: str) -> None:
        self.statusBar().showMessage(message)
        operation = self._google_drive_operation
        show_completion = self._google_drive_show_completion
        self._google_drive_operation = ""
        self._google_drive_show_completion = False
        if operation == "upload":
            self._google_drive_dirty = False
            self._settings.set("google_drive_unsynced_changes", "0")
            self._settings.set("google_drive_last_upload_success_at", self._google_drive_timestamp_now())
            self._google_drive_active_target = ""
        elif operation == "download" and "다운로드했습니다" in message:
            synced_at = self._google_drive_timestamp_now()
            self._settings.set("google_drive_unsynced_changes", "0")
            self._settings.set("google_drive_local_changed_at", synced_at)
            self._settings.set("google_drive_last_upload_success_at", synced_at)
            self._apply_downloaded_google_drive_data()
        self._refresh_google_drive_status()
        if operation == "upload" and self._google_drive_first_backup_pending:
            self._google_drive_first_backup_pending = False
            target = {"settings": "설정", "themes": "테마", "both": "설정과 테마"}.get(self._settings.get("google_drive_sync_target"), "설정과 테마")
            QMessageBox.information(self, "첫 Google Drive 백업", f"현재 컴퓨터의 {target}를 Google Drive에 업로드했습니다.")
            if self._initial_ranking_waits_for_google_drive:
                self._start_initial_ranking()
        elif show_completion:
            QMessageBox.information(self, "Google Drive 동기화 완료", message)
        if operation == "download" and "아직 동기화된 설정이 없습니다" in message:
            target = {"settings": "설정", "themes": "테마", "both": "설정과 테마"}.get(self._settings.get("google_drive_sync_target"), "설정과 테마")
            answer = QMessageBox.question(
                self,
                "첫 Google Drive 백업",
                f"이 Google Drive에는 아직 저장된 {target}가 없습니다.\n\n"
                f"현재 컴퓨터의 {target}를 지금 업로드할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._google_drive_dirty = True
                self._settings.set("google_drive_unsynced_changes", "1")
                self._settings.set("google_drive_local_changed_at", self._google_drive_timestamp_now())
                self._google_drive_first_backup_pending = True
                self._start_google_drive_sync("upload")
            elif self._initial_ranking_waits_for_google_drive:
                self._start_initial_ranking()
            return
        if self._google_drive_close_pending and operation != "upload":
            self._start_google_drive_sync("upload", close_after=True)

    def _apply_downloaded_google_drive_data(self) -> None:
        """실행 중 내려받은 설정·테마를 표와 예약 작업에 즉시 반영한다."""
        self._settings.clear_cache()
        if self._theme_store is not None:
            self._themes = self._theme_store.all_by_name()
        layout_changed = self._restore_columns() if self._columns is not None else False
        if layout_changed:
            self._table.updateGeometry()
            QTimer.singleShot(0, self._resize_columns_proportionally)
        if self._ranking_loader is not None and hasattr(self._ranking_loader, "set_query_type"):
            self._ranking_loader.set_query_type(self._settings.get("rank_query_type"))
        saved_rank_query = self._settings.get("rank_query_type")
        self._rank_query_selector.setCurrentIndex(("5", "1", "2", "3", "4").index(saved_rank_query) if saved_rank_query in {"1", "2", "3", "4", "5"} else 0)
        self._update_trade_display_headers()
        self._update_high_display_headers()
        for code in self._row_by_code:
            self._render_new_high_price(code)
            self._render_high_distance(code)
        self._apply_table_visuals()
        self._theme_trade_summary.setVisible(self._settings.get("theme_trade_summary_enabled") == "1")
        self._update_clock_label()
        if self._initial_ranking_waits_for_google_drive:
            self._start_initial_ranking()
        else:
            self._schedule_next_ranking_refresh()
            self._refresh_rankings()

    def _on_google_drive_sync_failed(self, message: str) -> None:
        logger.warning("Google Drive 동기화 실패: %s", message)
        self.statusBar().showMessage(f"Google Drive 동기화 실패: {message}")
        operation = self._google_drive_operation
        if operation == "upload":
            self._google_drive_dirty = True
            self._settings.set("google_drive_unsynced_changes", "1")
            if self._google_drive_active_target == "both":
                self._google_drive_pending_target = "both"
        self._google_drive_first_backup_pending = False
        self._google_drive_operation = ""
        self._google_drive_active_target = ""
        self._google_drive_show_completion = False
        self._refresh_google_drive_status()
        if operation in {"download", "metadata"} and self._initial_ranking_waits_for_google_drive:
            self._start_initial_ranking()

    def _refresh_google_drive_status(self) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.refresh_drive_status()

    def _change_rank_query_type(self) -> None:
        query_type = str(self._rank_query_selector.currentData())
        self._settings.set("rank_query_type", query_type)
        if self._ranking_loader is not None and hasattr(self._ranking_loader, "set_query_type"):
            self._ranking_loader.set_query_type(query_type)
        self._schedule_next_ranking_refresh()

    def _toggle_table_cell_selection(self, row: int, column: int) -> None:
        """클릭한 종목의 행 전체를 강조하고 같은 행을 다시 누르면 해제한다."""
        cell = (row, column)
        previous_code = self._selected_table_code
        stock_item = self._table.item(row, 1)
        selected_code = str(stock_item.data(Qt.ItemDataRole.UserRole) or "") if stock_item is not None else ""
        if self._selected_table_cell == cell:
            self._table.clearSelection()
            self._table.setCurrentItem(None)
            self._selected_table_cell = None
            self._selected_table_code = None
        else:
            self._selected_table_cell = cell
            self._selected_table_code = selected_code or None
            self._table.selectRow(row)
        delegate = self._table.itemDelegate()
        if isinstance(delegate, NxtMarkerDelegate):
            delegate.set_selected_cell(self._selected_table_cell)
        self._update_selected_column_header()
        if previous_code:
            self._apply_row_background(previous_code)
        if self._selected_table_code:
            self._apply_row_background(self._selected_table_code)
        self._table.viewport().update()

    def _update_selected_column_header(self) -> None:
        selected_column = self._selected_table_cell[1] if self._selected_table_cell is not None else -1
        for column in range(self._table.columnCount()):
            item = self._table.horizontalHeaderItem(column)
            if item is None:
                continue
            selected = column == selected_column
            font = item.font()
            font.setBold(selected)
            font.setUnderline(False)
            item.setFont(font)
            item.setForeground(QBrush())

    def _handle_main_table_click(self, row: int, column: int) -> None:
        self._toggle_table_cell_selection(row, column)
        if column == 1 and self._news_process_manager.is_running and self._news_window_is_visible():
            stock_item = self._table.item(row, 1)
            if stock_item is None:
                return
            code = str(stock_item.data(Qt.ItemDataRole.UserRole) or "")
            name = stock_item.text().strip()
            if not code or not name:
                return
            # 연속 클릭 중에는 뉴스 DB 확인조차 매번 시작하지 않고 마지막
            # 종목만 넘긴다. 순위가 바뀌어도 캡처한 코드/이름을 사용한다.
            request_id = self._news_command_channel.advance()
            QTimer.singleShot(
                80,
                lambda: self._apply_pending_news_selection(request_id, code, name),
            )

    def _apply_pending_news_selection(self, request_id: int, code: str, name: str) -> None:
        if request_id != self._news_command_channel.request_id:
            return
        if self._news_process_manager.is_running and self._news_window_is_visible():
            self._send_news_command(code, name, activate=False)

    def _news_window_is_visible(self) -> bool:
        if self._news_state_path is None:
            return False
        try:
            document = json.loads(self._news_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(document.get("visible", False))

    def _handle_main_table_double_click(self, row: int, column: int) -> None:
        if column == 1 and self._news_config_path is not None and self._news_database_path is not None:
            self._news_command_channel.advance()
            self._show_stock_news(row)
            return
        self._edit_theme_from_main_table(row, column)

    def _show_stock_news(self, row: int, *, activate: bool = True) -> None:
        if row < 0 or row >= self._table.rowCount():
            return
        stock_item = self._table.item(row, 1)
        if stock_item is None:
            return
        code = str(stock_item.data(Qt.ItemDataRole.UserRole) or "")
        name = stock_item.text().strip()
        if not code or not name or self._news_config_path is None or self._news_database_path is None:
            return
        self._ensure_news_process()
        self._send_news_command(code, name, activate=activate)

    def _show_trading_journal(self) -> None:
        """과거 매매목록을 별도 프로세스로 열어 메인 실시간 표를 보호한다."""
        row = self._selected_table_cell[0] if self._selected_table_cell is not None else self._table.currentRow()
        code = ""
        name = ""
        if row >= 0:
            item = self._table.item(row, 1)
            if item is not None:
                code = str(item.data(Qt.ItemDataRole.UserRole) or "")
                name = item.text().strip()
        self._send_journal_command(code, name)
        # 새 프로세스를 먼저 띄우면 시작 직후 이전 세션의 shutdown 명령을
        # 읽고 닫힐 수 있다. 최신 show 명령을 기록한 뒤 프로세스를 시작한다.
        self._ensure_journal_process()

    def _ensure_journal_process(self) -> None:
        if self._journal_process_manager.is_running:
            return
        if self._journal_process_path is not None:
            existing_pid, start_token = read_process_identity(self._journal_process_path)
            if process_identity_is_alive(existing_pid, start_token):
                return
        if self._journal_database_path is None or self._monitor_database_path is None or self._journal_command_path is None:
            return
        config = self._monitor_database_path.parent / "api.env"
        command = build_auxiliary_command("kiwoom_monitor.journal_process", "--journal-process", [
            "--monitor-database", str(self._monitor_database_path),
            "--journal-database", str(self._journal_database_path),
            "--config", str(config), "--command-file", str(self._journal_command_path),
            "--parent-pid", str(os.getpid()),
        ])
        try:
            self._journal_process_manager.start(command, self._monitor_database_path.parent.parent)
        except OSError as error:
            logger.warning("매매일지 프로세스를 시작하지 못했습니다: %s", error)
            self.statusBar().showMessage("매매일지를 시작하지 못했습니다.")

    def _send_journal_command(self, code: str = "", name: str = "", action: str = "show") -> None:
        if self._journal_command_path is None:
            return
        document = {
            "action": action,
            "code": code,
            "name": name,
            "parent_pid": os.getpid(),
        }
        try:
            self._journal_command_channel.send(document)
        except OSError as error:
            logger.warning("매매일지 명령을 저장하지 못했습니다: %s", error)

    def _stop_stale_journal_process(self) -> None:
        if self._journal_process_path is None or self._journal_command_path is None:
            return
        pid, start_token = read_process_identity(self._journal_process_path)
        if not process_identity_is_alive(pid, start_token):
            # 과거 PID 한 줄 파일은 동일 프로세스라는 증명이 불가능하다.
            # Windows가 재사용한 PID의 무관한 프로그램을 종료하지 않는다.
            try: self._journal_process_path.unlink(missing_ok=True)
            except OSError: pass
            return
        self._send_journal_command(action="shutdown")
        deadline = time.monotonic() + 2.0
        while process_identity_is_alive(pid, start_token) and time.monotonic() < deadline:
            time.sleep(0.05)
        if process_identity_is_alive(pid, start_token):
            try: os.kill(pid, 15)
            except OSError: pass
        try: self._journal_process_path.unlink(missing_ok=True)
        except OSError: pass

    def _stop_current_journal_process(self) -> None:
        if self._journal_command_path is None:
            return
        self._send_journal_command(action="shutdown")
        self._journal_process_manager.stop(graceful_timeout=2.0, terminate_timeout=1.0)

    def _ensure_news_process(self) -> None:
        if self._news_process_manager.is_running:
            return
        if self._news_config_path is None or self._news_database_path is None or self._news_command_path is None:
            return
        command = build_auxiliary_command("kiwoom_monitor.news_process", "--news-process", [
            "--config", str(self._news_config_path),
            "--database", str(self._news_database_path),
            "--command-file", str(self._news_command_path),
            "--parent-pid", str(os.getpid()),
        ])
        project_root = self._news_config_path.parent.parent
        working_directory = project_root if (project_root / "pyproject.toml").is_file() else Path(sys.executable).resolve().parent
        try:
            if self._news_state_path is not None:
                self._news_state_path.unlink(missing_ok=True)
            self._news_process_manager.start(command, working_directory)
        except OSError as error:
            logger.warning("뉴스 프로세스를 시작하지 못했습니다: %s", error)
            self.statusBar().showMessage("뉴스창을 시작하지 못했습니다.")

    def _stop_current_news_process(self) -> None:
        """메인 앱 종료 시 뉴스 자식 프로세스까지 확실히 정리한다."""
        process = self._news_process_manager.process
        if process is None:
            return
        self._news_process_manager.stop(
            request_shutdown=lambda: self._send_news_command(action="shutdown"),
            graceful_timeout=3.0,
            terminate_timeout=1.0,
            kill_timeout=1.0,
        )

    def _send_news_command(self, code: str = "", name: str = "", *, activate: bool = True,
                           action: str = "show", journal_group_id: str = "", trade_date: str = "") -> None:
        if self._news_command_path is None:
            return
        # 상·하·좌·우 고정은 제목 표시줄과 Windows 테두리까지 포함한 실제
        # 창 외곽을 기준으로 해야 한다. self.x/y/width/height는 내용 영역이라
        # 위·아래는 제목 표시줄만큼, 좌·우는 테두리만큼 서로 겹치게 된다.
        frame = self.frameGeometry()
        document = {
            "action": action,
            "code": code,
            "name": name,
            "activate": activate,
            "window_mode": self._news_window_mode(),
            "main_geometry": [frame.x(), frame.y(), frame.width(), frame.height()],
            "journal_group_id": journal_group_id,
            "trade_date": trade_date,
        }
        try:
            self._news_command_channel.send(document)
        except OSError as error:
            logger.warning("뉴스 프로세스 명령을 저장하지 못했습니다: %s", error)

    def _poll_journal_news_request(self) -> None:
        document = self._journal_news_inbox.read_new()
        if document is None:
            return
        code = str(document.get("code", "")); name = str(document.get("name", ""))
        if not code or not name:
            return
        self._ensure_news_process()
        self._send_news_command(
            code, name, journal_group_id=str(document.get("group_id", "")),
            trade_date=str(document.get("trade_date", "")),
        )

    @staticmethod
    def _news_window_mode() -> str:
        mode = str(QSettings("KiwoomMonitor", "StockNewsWindow").value("window_mode", "independent"))
        if mode == "docked":
            return "docked_right"
        valid = {"independent", "linked", "docked_right", "docked_left", "docked_top", "docked_bottom"}
        return mode if mode in valid else "independent"

    def _sync_news_window(self) -> None:
        if not self._news_process_manager.is_running:
            return
        mode = self._news_window_mode()
        if mode == "linked" or mode.startswith("docked_"):
            self._send_news_command(action="sync", activate=False)

    def _finish_news_restore_sync(self) -> None:
        self._news_restore_sync_pending = False
        self._sync_news_window()

    def _open_column_manager(self) -> None:
        if self._columns is None:
            return
        dialog = ColumnManagerDialog(self._columns, self.COLUMNS, self._table, self)
        if dialog.exec():
            self._apply_column_settings()

    def _apply_column_settings(self) -> None:
        if self._restore_columns():
            self._table.updateGeometry()
            QTimer.singleShot(0, self._resize_columns_proportionally)
        self.statusBar().showMessage("필드 편집을 적용했습니다.")

    def _open_alert_settings(self) -> None:
        if AlertSettingsDialog(self._settings, self).exec():
            enabled = self._settings.get("near_high_alert_enabled") == "1"
            self.statusBar().showMessage("신고가 근접 알림을 " + ("사용합니다." if enabled else "사용하지 않습니다."))

    def _open_theme_manager(self) -> None:
        if self._theme_store is not None:
            ThemeManagerDialog(self._theme_store, self._settings, self._select_excel, self._select_theme_image, self._sync_krx_stock_catalog, self, self._on_themes_changed).exec()

    def _set_api_status(self, text: str, color: str) -> None:
        self._api_status.setText(text)
        self._api_status.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _set_connected_api_status(self) -> None:
        if self._active_api_route == "local_fallback":
            self._set_api_status("API: 로컬 전환", "#B36B00")
        elif self._active_api_route == "central":
            self._set_api_status("API: NAS", "#008000")
        elif self._active_api_route == "central_waiting":
            self._set_api_status("API: NAS · 실시간 대기", "#B36B00")
        elif self._active_api_route == "central_retry":
            self._set_api_status("API: NAS 재연결 중…", "#B36B00")
        else:
            self._set_api_status("API: 연결됨", "#008000")

    def _on_realtime_status_changed(self, message: str) -> None:
        """실시간 수신 경로를 상단에 표시하고 일반 상태 문구도 유지한다."""
        self.statusBar().showMessage(message)
        if "로컬" in message and ("전환" in message or "실시간" in message):
            self._active_api_route = "local_fallback"
        elif "복구 여부" in message:
            self._active_api_route = "central_retry"
        elif "중앙 실시간 체결 구독 중" in message:
            self._active_api_route = "central"
        elif "시놀로지 서버 연결됨" in message and "원본 대기" in message:
            self._active_api_route = "central_waiting"
        else:
            return
        self._set_connected_api_status()

    def _open_log_file(self) -> None:
        # 설치본의 Program Files는 읽기 전용이다. 로그는 항상 사용자 데이터
        # 폴더(%LocalAppData%\\KiwoomMonitor\\data\\logs)를 연다.
        log_dir = AppPaths.for_current_user().log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.info("사용자가 로그 폴더를 열었습니다: %s", log_dir)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir))):
            self.statusBar().showMessage(f"로그 폴더를 열 수 없습니다: {log_dir}")

    def _export_settings_backup(self) -> None:
        default_name = f"키움_모니터_설정백업_{datetime.now():%Y%m%d}.json"
        path, _ = QFileDialog.getSaveFileName(self, "설정 백업 저장", str(Path.home() / "Documents" / default_name), "설정 백업 (*.json)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        try:
            SettingsBackupService(self._settings.database_path).export_to(
                target,
                include_themes=False,
                excluded_setting_keys=frozenset(
                    key for key in DEFAULT_SETTINGS if not is_shared_setting(key)
                ),
                include_column_widths=False,
            )
        except OSError as error:
            QMessageBox.warning(self, "설정 백업", f"설정 백업을 저장하지 못했습니다.\n{error}")
            return
        QMessageBox.information(
            self,
            "설정 백업",
            "공통 설정과 표 표시·순서를 저장했습니다.\n"
            "테마 DB, 창 위치·크기, 열 너비, API 키, 로그, 시세 데이터는 포함하지 않았습니다.",
        )

    def _import_settings_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "설정 백업 불러오기", "", "설정 백업 (*.json)")
        if not path:
            return
        if QMessageBox.question(
            self,
            "설정 복원",
            "현재 공통 설정과 표 표시·순서를 백업 파일 내용으로 바꿉니다. "
            "테마 DB와 이 PC의 창 위치·크기는 바뀌지 않습니다. 계속할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            SettingsBackupService(self._settings.database_path).import_from(
                Path(path),
                include_themes=False,
                excluded_setting_keys=frozenset(
                    key for key in DEFAULT_SETTINGS if not is_shared_setting(key)
                ),
                include_column_widths=False,
            )
        except SettingsBackupError as error:
            QMessageBox.warning(self, "설정 복원", str(error))
            return
        self._apply_downloaded_google_drive_data()
        QMessageBox.information(self, "설정 복원", "설정을 복원하고 바로 적용했습니다.")

    def _export_theme_backup(self) -> None:
        default_name = f"키움_모니터_테마DB_{datetime.now():%Y%m%d}.json"
        path, _ = QFileDialog.getSaveFileName(self, "테마 DB 저장", str(Path.home() / "Documents" / default_name), "테마 DB 백업 (*.json)")
        if not path:
            return
        target = Path(path).with_suffix(".json")
        try:
            ThemeBackupService(self._settings.database_path).export_to(target)
        except OSError as error:
            QMessageBox.warning(self, "테마 DB 저장", f"테마 DB 백업을 저장하지 못했습니다.\n{error}")
            return
        QMessageBox.information(self, "테마 DB 저장", "모든 테마 프로필의 테마·종목 연결·테마 색·별칭·등록 종목을 저장했습니다.\n일반 설정, 필드 구성, API 키, 로그는 포함하지 않았습니다.")

    def _import_theme_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "테마 DB 불러오기", "", "테마 DB 백업 (*.json)")
        if not path:
            return
        if QMessageBox.question(self, "테마 DB 불러오기", "모든 테마 프로필의 테마·종목 연결·테마 색·별칭을 백업 내용으로 바꿉니다. 일반 설정은 바뀌지 않습니다. 계속할까요?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            ThemeBackupService(self._settings.database_path).import_from(Path(path))
        except ThemeBackupError as error:
            QMessageBox.warning(self, "테마 DB 불러오기", str(error))
            return
        if self._theme_store is not None:
            profiles = self._theme_store.list_profiles()
            selected = self._settings.get("theme_active_profile")
            selected = selected if selected in profiles else profiles[0]
            self._theme_store.select_profile(selected)
            self._settings.set("theme_active_profile", selected)
        self._on_themes_changed()
        QMessageBox.information(self, "테마 DB 불러오기", "모든 테마 프로필을 불러왔습니다.")

    def _export_journal_backup(self) -> None:
        if self._journal_database_path is None:
            return
        default = Path.home() / "Documents" / f"키움_매매일지백업_{datetime.now():%Y%m%d}.sqlite3"
        path, _ = QFileDialog.getSaveFileName(self, "매매일지 백업 저장", str(default), "매매일지 백업 (*.sqlite3)")
        if not path:
            return
        target = Path(path).with_suffix(".sqlite3")
        try:
            JournalBackupService(self._journal_database_path).export_to(target)
        except (OSError, sqlite3.Error) as error:
            QMessageBox.warning(self, "매매일지 백업", f"백업하지 못했습니다.\n{error}"); return
        QMessageBox.information(self, "매매일지 백업", "체결·분봉·일봉·복기·분석·그리기 자료를 저장했습니다.")

    def _import_journal_backup(self) -> None:
        if self._journal_database_path is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "매매일지 백업 불러오기", "", "매매일지 백업 (*.sqlite3)")
        if not path or QMessageBox.question(self, "매매일지 복원", "현재 매매일지 전체를 백업 내용으로 바꿀까요?") != QMessageBox.StandardButton.Yes:
            return
        if self._journal_process_manager.is_running:
            self._send_journal_command(action="shutdown")
            process = self._journal_process_manager.process
            try:
                if process is not None:
                    process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                QMessageBox.warning(self, "매매일지 복원", "매매일지 창을 닫은 뒤 다시 시도하세요."); return
        try:
            JournalBackupService(self._journal_database_path).import_from(Path(path))
        except (OSError, sqlite3.Error, ValueError) as error:
            QMessageBox.warning(self, "매매일지 복원", str(error)); return
        self._journal_process_manager.clear()
        QMessageBox.information(self, "매매일지 복원", "매매일지 자료를 복원했습니다.")

    def _on_background_failure(self, message: str) -> None:
        logger.warning("백그라운드 작업 실패: %s", message)
        self.statusBar().showMessage(f"작업 일부 실패: {message} · 로그 열기에서 자세한 내용을 확인하세요.")

    def _edit_theme_from_main_table(self, row: int, column: int) -> None:
        if column != 2:
            return
        if self._theme_store is None or row < 0 or row >= self._table.rowCount():
            return
        stock_item = self._table.item(row, 1)
        if stock_item is None:
            return
        display = stock_item.text()
        code = str(stock_item.data(Qt.ItemDataRole.UserRole) or "")
        if not code:
            return
        name = display.strip()
        before_themes = self._theme_store.themes_for_stock(code)
        before = ", ".join(before_themes)
        dialog = ThemeEditDialog(name, before_themes, ",/|;" + self._settings.get("theme_custom_separators"), self)
        if not dialog.exec():
            return
        after = dialog.themes
        if QMessageBox.question(
            self,
            "테마 변경 확인",
            f"종목: {name}\n\n기존: {before or '-'}\n변경: {', '.join(after) or '-'}\n\n변경사항을 저장할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self._theme_store.replace_for_stock(code, after)
            self._on_themes_changed()

    @staticmethod
    def _api_config_path() -> Path:
        return AppPaths.for_current_user().data_dir / "api.env"

    def _restore_environment_selector(self) -> None:
        path = self._api_config_path()
        if not path.exists():
            self._environment_selector.setEnabled(False)
            return
        try:
            active = LocalApiConfig(path).load_profiles().active_environment
            self._environment_selector.setCurrentIndex(0 if active == "mock" else 1)
        except ValueError:
            self._environment_selector.setEnabled(False)

    def _change_environment(self) -> None:
        path = self._api_config_path()
        if not path.exists():
            return
        try:
            profiles = LocalApiConfig(path).load_profiles()
        except ValueError:
            return
        environment = str(self._environment_selector.currentData())
        if environment == profiles.active_environment:
            return
        credentials = (profiles.real_app_key, profiles.real_secret_key) if environment == "real" else (profiles.mock_app_key, profiles.mock_secret_key)
        if not all(credentials):
            QMessageBox.warning(self, "실행 환경", "선택한 환경의 API 키가 없습니다. API 설정에서 먼저 입력하세요.")
            self._environment_selector.blockSignals(True)
            self._environment_selector.setCurrentIndex(0 if profiles.active_environment == "mock" else 1)
            self._environment_selector.blockSignals(False)
            return
        LocalApiConfig(path).save_profiles(replace(profiles, active_environment=environment))
        self._restart_for_api_settings()

    def _open_api_settings(self) -> None:
        if self._closing:
            return
        path = self._api_config_path()
        dialog = ApiSettingsDialog(path, self, active_route=self._active_api_route)
        if dialog.exec():
            LocalApiConfig(path).save_profiles(dialog.values)
            self._restart_for_api_settings()

    def _restart_for_api_settings(self) -> None:
        """앱을 끄지 않고 새 API 설정으로 연결 작업을 다시 구성한다."""
        if self._api_runtime_factory is None:
            self.statusBar().showMessage("API 설정이 저장되었습니다. 앱을 다시 열면 적용됩니다.")
            return
        if self._api_reloading:
            return
        self._api_reloading = True
        self._ranking_timer.stop()
        self._ranking_preparation_timer.stop()
        self._realtime_session_timer.stop()
        self._ranking_execution.cancel_pending_request()
        # 기존 API 응답이 새 설정의 화면을 다시 덮어쓰지 않도록, 작업 정리
        # 중에는 후속 보완 조회 연결도 잠시 보류한다.
        self._ranking_execution.begin_priority_preparation()
        for worker in self._api_runtime_workers():
            worker.requestInterruption()
        self._set_api_status("API: 설정 적용 중…", "#B36B00")
        self.statusBar().showMessage("새 API 설정을 적용하는 중입니다…")
        QTimer.singleShot(50, self._finish_api_runtime_reload)

    def _api_runtime_workers(self) -> tuple[QThread, ...]:
        return tuple(
            worker
            for worker in (
                self._realtime_worker,
                self._minute_history_worker,
                self._fundamentals_worker,
                self._daily_high_worker,
                self._historical_high_worker,
                self._nxt_eligibility_worker,
                self._new_high_worker,
                self._ranking_worker,
            )
            if worker is not None and worker.isRunning()
        )

    def _finish_api_runtime_reload(self) -> None:
        if self._closing or not self._api_reloading:
            return
        if self._api_runtime_workers():
            self.statusBar().showMessage("이전 API 작업을 정리하는 중입니다…")
            QTimer.singleShot(100, self._finish_api_runtime_reload)
            return
        try:
            runtime = self._api_runtime_factory() if self._api_runtime_factory is not None else {}
            self._ranking_loader = runtime.get("ranking_loader")  # type: ignore[assignment]
            self._realtime_worker_factory = runtime.get("realtime_worker_factory")  # type: ignore[assignment]
            self._realtime_worker_controller.set_factory(self._realtime_worker_factory)
            self._minute_history_worker_factory = runtime.get("minute_history_worker_factory")  # type: ignore[assignment]
            self._minute_history_worker_controller.set_factory(
                self._minute_history_worker_factory
            )
            self._fundamentals_worker_factory = runtime.get("fundamentals_worker_factory")  # type: ignore[assignment]
            self._fundamentals_worker_controller.set_factory(
                self._fundamentals_worker_factory
            )
            self._daily_high_worker_factory = runtime.get("daily_high_worker_factory")  # type: ignore[assignment]
            self._daily_high_worker_controller.set_factory(
                self._daily_high_worker_factory
            )
            self._historical_high_worker_controller.set_factory(
                runtime.get("historical_high_worker_factory")  # type: ignore[arg-type]
            )
            self._nxt_eligibility_worker_controller.set_factory(
                runtime.get("nxt_eligibility_worker_factory")  # type: ignore[arg-type]
            )
            if self._entry_snapshot_writer is not None:
                self._entry_snapshot_writer.set_investor_loader(runtime.get("entry_investor_loader"))  # type: ignore[arg-type]
                self._entry_snapshot_writer.set_program_loader(runtime.get("program_trade_loader"))  # type: ignore[arg-type]
        except Exception as error:
            self._api_reloading = False
            self._ranking_execution.end_priority_preparation()
            self._set_api_status("API: 오류", "#C00000")
            QMessageBox.warning(self, "API 설정", f"새 API 설정을 적용하지 못했습니다.\n{error}")
            return

        self._realtime_subscription.reset()
        self._minute_history_codes.clear()
        self._minute_aggregator = MinuteTradeValueAggregator()
        self._initial_new_high_refresh_started = False
        self._initial_nxt_codes = ()
        self._api_reloading = False
        self._ranking_execution.end_priority_preparation()
        self._restore_environment_selector()
        self.statusBar().showMessage("새 API 설정 적용 완료 · 순위를 다시 조회합니다.")
        self._start_initial_ranking()

    def _select_theme_image(self, mode: str = "theme_column") -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "테마 이미지 선택", self._settings.get("theme_image_import_dir"), "이미지 파일 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not paths:
            return
        image_paths = tuple(Path(path) for path in paths)
        self._settings.set("theme_image_import_dir", str(image_paths[0].parent))
        theme_header = self._settings.get("theme_image_import_theme_header").strip()
        self._start_image_theme_ocr(image_paths, mode, theme_header)

    def _start_image_theme_ocr(self, image_paths: tuple[Path, ...], mode: str = "theme_column", theme_header: str = "테마") -> None:
        if self._image_theme_ocr_worker_controller.is_running:
            QMessageBox.information(self, "이미지 OCR", "이미지 분석이 이미 진행 중입니다.")
            return
        progress = QProgressDialog("OCR 엔진을 준비하고 있습니다…", "취소", 0, 0, self)
        progress.setWindowTitle("이미지 테마 분석")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._image_theme_ocr_progress_dialog = progress
        # OCR 확인·수정 창을 오래 열어 두어도 실시간 표 갱신은 계속한다.
        self._image_theme_workflow_active = True
        progress.canceled.connect(self._image_theme_ocr_worker_controller.request_interruption)
        self.statusBar().showMessage(f"이미지 {len(image_paths)}장의 OCR 모델을 준비하고 있습니다. 첫 실행은 모델 다운로드로 시간이 걸릴 수 있습니다.")
        progress.show()
        # OCR은 CPU 사용량이 큰 보조 작업이다. 실시간 표/UI보다 낮은 우선순위로 실행한다.
        self._image_theme_ocr_worker_controller.start(image_paths, mode, theme_header)
        QTimer.singleShot(60_000, self._warn_slow_image_ocr)

    def _on_image_theme_ocr_progress(self, message: str) -> None:
        progress = getattr(self, "_image_theme_ocr_progress_dialog", None)
        if progress is not None:
            progress.setLabelText(message)
        self.statusBar().showMessage(message)

    def _close_image_theme_ocr_progress(self) -> None:
        progress = getattr(self, "_image_theme_ocr_progress_dialog", None)
        if progress is not None:
            progress.close()
            self._image_theme_ocr_progress_dialog = None

    def _on_image_theme_ocr_failed(self, message: str) -> None:
        self._close_image_theme_ocr_progress()
        self._image_theme_workflow_active = False
        QMessageBox.warning(self, "이미지 OCR 실패", message)

    def _on_image_theme_ocr_finished(self) -> None:
        # 정상 완료 뒤에는 결과 검토창의 중첩 이벤트 루프가 먼저 열릴 수 있다.
        # 취소되어 결과창이 열리지 않은 경우에만 여기서 상태를 정리한다.
        if not bool(getattr(self, "_image_theme_rows_reviewing", False)):
            self._image_theme_workflow_active = False

    def _with_krx_stock_catalog(self, continuation: Callable[[], None]) -> None:
        if self._stock_lookup is None or not hasattr(self._stock_lookup, "upsert_many"):
            continuation(); return
        def completed(count: int, cached: bool) -> None:
            self.statusBar().showMessage("KRX 상장종목 목록 확인 완료" if cached else f"KRX 상장종목 {count:,}개 갱신 완료")
            self._review_pending_stock_name_changes()
            continuation()
        self.statusBar().showMessage("KRX 전체 상장종목 목록을 확인하고 있습니다.")
        self._krx_stock_catalog_worker_controller.start(
            self._stock_lookup,
            self._settings,
            completed=completed,
            history_failed=lambda message: logger.warning("KIND 상호변경 이력 동기화 실패: %s", message),
            failed=lambda message: (QMessageBox.warning(self, "KRX 상장종목 목록", f"전체 목록을 갱신하지 못했습니다.\n저장된 목록으로 계속합니다.\n\n{message}"), continuation()),
        )

    def _sync_krx_stock_catalog(self) -> None:
        if self._stock_lookup is None or not hasattr(self._stock_lookup, "upsert_many"):
            return
        def completed(count: int, cached: bool) -> None:
            QMessageBox.information(self, "상장종목 목록 동기화", f"동기화 완료\n성공 날짜: {self._settings.get('krx_stock_catalog_date')}\n{'오늘 이미 받은 목록입니다.' if cached else f'{count:,}개 종목을 갱신했습니다.'}")
            self._review_pending_stock_name_changes()
        self.statusBar().showMessage("KRX 전체 상장종목 목록을 동기화하고 있습니다.")
        self._krx_stock_catalog_worker_controller.start(
            self._stock_lookup,
            self._settings,
            completed=completed,
            history_failed=lambda message: QMessageBox.warning(self, "상호변경 이력 동기화", f"상장종목 목록은 갱신됐지만 KIND 상호변경 이력은 받지 못했습니다.\n\n{message}"),
            failed=lambda message: QMessageBox.warning(self, "상장종목 목록 동기화 실패", message),
        )

    def _start_daily_krx_catalog_sync(self) -> None:
        """상장종목 목록은 하루 한 번, 모든 주식 API 보완 뒤에만 갱신한다."""
        if self._closing or self._ranking_execution.priority_preparing or self._stock_lookup is None or not hasattr(self._stock_lookup, "upsert_many"):
            return
        if self._krx_stock_catalog_worker_controller.is_running:
            return
        today = self._ranking_now().strftime("%Y-%m-%d")
        visible_codes = tuple(self._row_by_code)
        # KRX 법인 목록에는 ETF·ETN·일부 우선주가 없어 이 종목들은 다시
        # 받아도 market이 채워지지 않는다. 잔여 미분류를 일일 갱신 조건으로
        # 쓰면 해당 종목이 TOP20에 보일 때마다 2,700여 행을 다시 저장해
        # 메인 SQLite 조회까지 잠금 대기시키므로 성공 날짜만 기준으로 삼는다.
        if not daily_catalog_sync_due(
            self._settings.get("krx_stock_catalog_date"), today,
        ):
            if not self._is_after_hours_data_pause():
                self._start_historical_high_loading(visible_codes)
            return
        def completed(count: int, _cached: bool) -> None:
            self.statusBar().showMessage(f"KRX 상장종목 {count:,}개 자동 동기화 완료")
            if hasattr(self._stock_lookup, "load_markets"):
                self._stock_markets.update(self._stock_lookup.load_markets(
                    self._top20_realtime_codes(tuple(self._row_by_code))
                ))
            self._top20_repair_last_started = 0.0
            self._start_top20_market_repair()
            self._review_pending_stock_name_changes()
        self.statusBar().showMessage("최하위 작업: KRX 전체 상장종목 목록 동기화 중")
        self._krx_stock_catalog_worker_controller.start(
            self._stock_lookup,
            self._settings,
            completed=completed,
            history_failed=lambda message: logger.warning("KIND 상호변경 이력 자동 동기화 실패: %s", message),
            failed=lambda message: logger.warning("KRX 상장종목 자동 동기화 실패: %s", message),
            finished=lambda: None if self._is_after_hours_data_pause() else self._start_historical_high_loading(tuple(self._row_by_code)),
        )

    def _review_pending_stock_name_changes(self) -> None:
        lookup = self._stock_lookup
        if lookup is None or not hasattr(lookup, "pending_name_changes") or not hasattr(lookup, "review_name_changes"):
            return
        changes = lookup.pending_name_changes()
        if not changes:
            return
        dialog = StockNameChangeReviewDialog(changes, self)
        if dialog.exec():
            lookup.review_name_changes(dialog.decisions())
            approved = sum(dialog.decisions().values())
            self.statusBar().showMessage(f"종목명 변경 확인 완료: {approved:,}개 과거 이름 연결")

    def _warn_slow_image_ocr(self) -> None:
        if not self._image_theme_ocr_worker_controller.is_running:
            return
        self.statusBar().showMessage("OCR 모델 다운로드가 1분 이상 걸리고 있습니다. 네트워크 연결을 확인하거나 취소할 수 있습니다.")
        if QMessageBox.question(self, "OCR 다운로드 지연", "한국어 OCR 모델 다운로드가 1분 이상 걸리고 있습니다.\n\n계속 기다릴까요?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.No:
            self._image_theme_ocr_worker_controller.request_interruption()

    def _show_image_theme_rows(self, rows: object) -> None:
        if not isinstance(rows, tuple):
            self._image_theme_workflow_active = False
            return
        self._image_theme_rows_reviewing = True
        try:
            dialog = ImageThemeRowsDialog(rows, self, self._settings)
            if not dialog.exec():
                return
            separators = ",/|;" + self._settings.get("theme_image_import_custom_separators")
            imported, errors = validate_theme_rows(
                self._filter_import_exclusions(dialog.rows(), separators, "theme_image_import_exclusions"),
                separators,
            )
            if errors:
                QMessageBox.warning(self, "이미지 테마 확인", "\n".join(errors))
                return
            matched, unmatched = match_theme_rows(imported, self._stock_lookup) if self._stock_lookup else ((), imported)
            resolved, cancelled = self._resolve_unmatched_theme_rows(unmatched, "이미지 OCR")
            if cancelled:
                return
            changes = preview_theme_changes(matched + resolved, self._theme_store) if self._theme_store else ()
            preview = ThemePreviewDialog(changes, len(unmatched) - len(resolved), self, frozenset(theme_key(theme) for theme in parse_themes(self._settings.get("theme_image_import_exclusions"), separators)))
            if preview.exec() and self._theme_store:
                changes = preview.changes(separators)
                pending = tuple((change.code, change.after) for change in changes if change.status != "변경 없음")
                applied = len(pending)
                replace_many = getattr(self._theme_store, "replace_many", None)
                if callable(replace_many):
                    replace_many(pending)
                else:
                    for code, themes in pending:
                        self._theme_store.replace_for_stock(code, themes)
                self._themes = self._theme_store.all_by_name()
                self._refresh_rankings()
                QMessageBox.information(self, "이미지 테마 업데이트 완료", f"{applied}개 종목의 테마를 적용했습니다.")
            self.statusBar().showMessage(f"이미지 테마 결과 · {len(changes)}개 확인 · 적용은 최종 확인 후에만 수행됩니다")
        finally:
            self._image_theme_rows_reviewing = False
            self._image_theme_workflow_active = False

    def _select_excel(self) -> None:
        self._choose_excel_after_catalog()

    def _choose_excel_after_catalog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "테마 Excel 선택", self._settings.get("theme_excel_import_dir"), "Excel 파일 (*.xlsx)")
        if path:
            self._settings.set("theme_excel_import_dir", str(Path(path).parent))
            try:
                source = ExcelThemeRepository(Path(path)); header, raw_rows = source.load_header_and_rows()
                errors = validate_theme_header(header)
            except Exception as error:
                self.statusBar().showMessage(f"Excel 읽기 실패: {error}")
                return
            if errors:
                QMessageBox.warning(self, "Excel 검증 오류", "\n".join(errors))
                return
            editor = ImageThemeRowsDialog(raw_rows, self, self._settings, "theme_excel_import")
            editor.setWindowTitle("Excel 테마 수정")
            labels = editor.findChildren(QLabel)
            if labels:
                labels[0].setText("Excel에서 읽은 종목명과 테마를 수정하세요. 구분자와 제외 테마는 Excel 업데이트에만 저장됩니다.")
            if not editor.exec():
                return
            separators = ",/|;" + self._settings.get("theme_excel_import_custom_separators")
            rows, errors = validate_theme_rows(
                self._filter_import_exclusions(editor.rows(), separators, "theme_excel_import_exclusions"),
                separators,
            )
            self.statusBar().showMessage(f"Excel 검증 완료 · 유효 {len(rows)}건 · 오류 {len(errors)}건")
            if errors:
                QMessageBox.warning(self, "Excel 검증 오류", "\n".join(errors))
            else:
                matched, unmatched = match_theme_rows(rows, self._stock_lookup) if self._stock_lookup else ((), rows)
                resolved, cancelled = self._resolve_unmatched_theme_rows(unmatched, "Excel")
                if cancelled:
                    return
                matched = matched + resolved
                changes = preview_theme_changes(matched, self._theme_store) if self._theme_store else ()
                changed = sum(change.status == "테마 변경" for change in changes); new = sum(change.status == "신규" for change in changes)
                preview = ThemePreviewDialog(changes, len(unmatched), self, frozenset(theme_key(theme) for theme in parse_themes(self._settings.get("theme_excel_import_exclusions"), separators)))
                if preview.exec() and self._theme_store:
                    changes = preview.changes(separators)
                    applied = sum(change.status != "변경 없음" for change in changes)
                    for change in changes:
                        if change.status != "변경 없음": self._theme_store.replace_for_stock(change.code, change.after)
                    self._themes = self._theme_store.all_by_name()
                    self._refresh_rankings()
                    QMessageBox.information(self, "Excel 테마 업데이트 완료", f"{applied}개 종목의 테마를 적용했습니다.")
                unchanged = sum(change.status == "변경 없음" for change in changes)
                self.statusBar().showMessage(f"Excel 결과 · 전체 {len(raw_rows)} · 변경 없음 {unchanged} · 신규 {new} · 테마 변경 {changed} · 오류/제외 {len(unmatched) - len(resolved)}")

    def _filter_import_exclusions(
        self,
        rows: tuple[tuple[str, str], ...],
        separators: str,
        setting_key: str = "theme_import_exclusions",
    ) -> tuple[tuple[str, str], ...]:
        excluded = {theme_key(theme) for theme in parse_themes(self._settings.get(setting_key), separators)}
        if not excluded:
            return rows
        filtered: list[tuple[str, str]] = []
        for name, value in rows:
            themes = tuple(theme for theme in parse_themes(value, separators) if theme_key(theme) not in excluded)
            if themes:
                filtered.append((name, "/".join(themes)))
        return tuple(filtered)

    def _resolve_unmatched_theme_rows(self, rows: tuple[object, ...], source_label: str) -> tuple[tuple[MatchedThemeRow, ...], bool]:
        if not rows or self._stock_lookup is None:
            return (), False
        resolved: list[MatchedThemeRow] = []
        for row in rows:
            original_name = str(getattr(row, "name", ""))
            themes = tuple(getattr(row, "themes", ()))
            renamed, handled = confirm_pending_name_change(self, self._stock_lookup, original_name, themes)
            if handled:
                if renamed is not None:
                    code, current_name = renamed
                    resolved.append(MatchedThemeRow(code, current_name, themes))
                continue
            split_finder = getattr(self._stock_lookup, "find_concatenated_stocks", None)
            split = split_finder(original_name) if callable(split_finder) else ()
            if split:
                labels = " + ".join(name for _, name in split)
                if QMessageBox.question(
                    self,
                    "붙어 있는 종목명 확인",
                    f"{source_label}의 '{original_name}'을(를) 다음 종목들로 나눌 수 있습니다.\n\n"
                    f"{labels}\n\n이대로 나눌까요?",
                ) == QMessageBox.StandardButton.Yes:
                    resolved.extend(MatchedThemeRow(code, name, themes) for code, name in split)
                    continue
            partial_finder = getattr(self._stock_lookup, "find_partial_concatenated_stocks", None)
            known, fragments = partial_finder(original_name) if callable(partial_finder) else ((), ())
            if known and fragments:
                known_labels = " + ".join(name for _, name in known)
                fragment_labels = ", ".join(fragments)
                if QMessageBox.question(
                    self,
                    "붙어 있는 종목명 일부 확인",
                    f"{source_label}의 '{original_name}'에서 다음 종목은 확인됐습니다.\n\n{known_labels}\n\n"
                    f"남은 이름만 다시 찾습니다: {fragment_labels}\n\n계속할까요?",
                ) == QMessageBox.StandardButton.Yes:
                    resolved.extend(MatchedThemeRow(code, name, themes) for code, name in known)
                    for fragment in fragments:
                        candidate, cancelled = choose_similar_stock(self, self._stock_lookup, fragment, themes)
                        if cancelled:
                            return (), True
                        if candidate:
                            code, selected_name = candidate
                            resolved.append(MatchedThemeRow(code, selected_name, themes))
                    continue
            while True:
                name, ok = QInputDialog.getText(
                    self,
                    "키움 종목명 확인",
                    f"{source_label}에서 읽은 '{original_name}' 종목명이 현재 키움 종목 목록에 없습니다.\n"
                    f"이 종목이 있던 테마: {', '.join(themes) or '없음'}\n"
                    "OCR 오인식이거나 키움의 실제 표기와 다른 이름일 수 있습니다.\n"
                    "키움에 표시되는 정확한 종목명으로 수정하세요. 비워 두면 이번 업데이트에서 제외합니다.",
                    text=original_name,
                )
                if not ok:
                    return (), True
                name = name.strip()
                if not name:
                    break
                code = self._stock_lookup.find_code_by_name(name)
                if code:
                    if original_name != name and hasattr(self._stock_lookup, "save_alias"):
                        self._stock_lookup.save_alias(original_name, code)
                    resolved.append(MatchedThemeRow(code, name, tuple(getattr(row, "themes", ()))))
                    break
                candidate, cancelled = choose_similar_stock(self, self._stock_lookup, name, themes)
                if candidate:
                    code, selected_name = candidate
                    if original_name != selected_name and hasattr(self._stock_lookup, "save_alias"):
                        self._stock_lookup.save_alias(original_name, code)
                    resolved.append(MatchedThemeRow(code, selected_name, tuple(getattr(row, "themes", ()))))
                    break
                if cancelled:
                    return (), True
                break
                choice = QMessageBox(self)
                choice.setWindowTitle("종목명 확인")
                choice.setText(f"'{name}'은(는) 저장된 전체 상장종목 목록에서 찾지 못했습니다.")
                choice.setInformativeText("다시 입력하거나, 이번 종목만 제외하고 나머지 업데이트를 계속할 수 있습니다.")
                retry = choice.addButton("다시 입력", QMessageBox.ButtonRole.AcceptRole)
                skip = choice.addButton("이번 종목 무시", QMessageBox.ButtonRole.DestructiveRole)
                cancel_all = choice.addButton("전체 취소", QMessageBox.ButtonRole.RejectRole)
                choice.exec()
                if choice.clickedButton() is skip:
                    break
                if choice.clickedButton() is cancel_all:
                    return (), True
        return tuple(resolved), False

    def _restore_columns(self) -> bool:
        return self._column_controller.restore()

    def _save_columns(self) -> None:
        self._column_controller.save()

    def _on_column_resized(self, *_: object) -> None:
        self._column_controller.section_resized()

    def _apply_uniform_row_height(self, height: int) -> None:
        """한 행 조절값을 표 전체 행에 적용하고 로컬에 저장한다."""
        height = clamp_uniform_row_height(height)
        # 열 너비와 동일하게 직접 조절한 뒤 30초 동안은 현재 크기를
        # 유지하고, 이후 창 크기를 바꾸면 반응형 자동 맞춤으로 복귀한다.
        self._column_controller.hold_manual_size()
        self._syncing_row_heights = True
        try:
            header = self._table.verticalHeader()
            header.setDefaultSectionSize(height)
            for row in range(self._table.rowCount()):
                if self._table.rowHeight(row) != height:
                    self._table.setRowHeight(row, height)
        finally:
            self._syncing_row_heights = False
        self._settings.set("ui_row_height", str(height))
        # 행 높이를 직접 드래그한 순간에도 글자·아이콘·테마 배지를 함께
        # 다시 계산한다. 다음 창 크기 변경까지 기다리지 않는다.
        self._apply_table_visuals()

    def _rank_row_resize_target(self, point: QPoint) -> bool:
        """순위 칸의 행 경계 근처인지 판별한다."""
        if self._table.rowCount() <= 0:
            return False
        # 경계선 바로 위·아래 어느 쪽에 마우스가 있어도 인식한다. 실제
        # 조절은 순위 칸 안에서만 가능하므로 다른 셀 클릭과 겹치지 않는다.
        for y in (point.y() - 7, point.y(), point.y() + 7):
            row = self._table.rowAt(max(0, y))
            if row < 0:
                continue
            rect = self._table.visualRect(self._table.model().index(row, 0))
            if rect.left() <= point.x() <= rect.right() and abs(point.y() - rect.bottom()) <= 7:
                return True
        return False

    def _refresh_theme_badges(self) -> None:
        """현재 행·글자 크기에 맞춰 표시 중인 테마 배지를 다시 만든다."""
        for code, row in self._row_by_code.items():
            name = self._ranked_stock_names.get(code)
            if name:
                self._table.setCellWidget(row, 2, self._theme_badges(code, name))

    def _resize_columns_proportionally(self) -> None:
        if not hasattr(self, "_table"):
            return
        self._column_controller.resize_proportionally(
            responsive=self._settings.get("ui_mode") == "responsive"
        )

    def _enable_initial_column_auto_fit(self) -> None:
        """초기 복원 완료 뒤부터 실제 창 크기 변경에만 자동 맞춤을 허용한다."""
        self._column_controller.enable_initial_auto_fit()

    def _show_column_menu(self, point: object) -> None:
        menu = QMenu(self)
        selected_column = self._table.horizontalHeader().logicalIndexAt(point)
        for logical, (_, label) in enumerate(self.COLUMNS):
            action = menu.addAction(label); action.setCheckable(True); action.setChecked(not self._table.isColumnHidden(logical))
            action.toggled.connect(lambda checked, index=logical: self._set_column_visible(index, checked))
        menu.addSeparator()
        fit = menu.addAction("전체 컬럼 자동 맞춤")
        fit.triggered.connect(self._enable_column_auto_fit)
        if selected_column >= 0:
            fit_selected = menu.addAction("선택 열 자동 맞춤")
            fit_selected.triggered.connect(lambda: (self._table.resizeColumnToContents(selected_column), self._save_columns()))
        reset = menu.addAction("컬럼 설정 초기화")
        reset.triggered.connect(self._reset_columns)
        menu.exec(self._table.horizontalHeader().mapToGlobal(point))

    def _set_column_visible(self, index: int, visible: bool) -> None:
        self._column_controller.set_visible(
            index, visible,
            responsive=self._settings.get("ui_mode") == "responsive",
        )
        QTimer.singleShot(0, self._resize_columns_proportionally)

    def _enable_column_auto_fit(self) -> None:
        self._column_controller.auto_fit(
            responsive=self._settings.get("ui_mode") == "responsive"
        )

    def _reset_columns(self) -> None:
        if self._columns is None:
            return
        self._column_controller.reset()
        self.statusBar().showMessage("컬럼 표시, 순서, 폭을 기본값으로 초기화했습니다.")

    def _on_ranking_timer(self) -> None:
        # 순위 기준 시각에는 반드시 이 요청을 먼저 시작한다. 직전 준비 단계에서
        # 보완 조회를 멈춰 두었기 때문에 REST 연결 대기 가능성을 최소화한다.
        now = self._ranking_now()
        worker_running = self._ranking_worker_controller.is_running
        if not self._ranking_execution.ranking_timer_fired(worker_running=worker_running):
            # 기준 시각에 겹친 요청을 없애지 않는다. 기존 요청이 끝나는 즉시
            # 한 번 더 조회해 순위 갱신 회차가 빠지는 일을 막는다.
            logger.warning("순위 기준 시각 %s: 이전 순위 조회가 진행 중이라 완료 직후 재조회합니다.", now.strftime("%H:%M:%S"))
            return
        logger.info("순위 기준 시각 %s: 순위 조회를 시작합니다.", now.strftime("%H:%M:%S"))
        self._refresh_rankings()

    def _prepare_ranking_refresh(self) -> None:
        """순위 기준 시각 직전에 저우선순위 REST 보완 요청을 양보시킨다."""
        if self._closing:
            return
        self._ranking_execution.begin_priority_preparation()
        for worker in (
            self._minute_history_worker,
            self._daily_high_worker,
            self._fundamentals_worker,
            self._nxt_eligibility_worker,
            self._krx_stock_catalog_worker,
        ):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()

    def _schedule_next_ranking_refresh(self) -> None:
        if self._closing or self._ranking_loader is None:
            return
        query_type = self._settings.get("rank_query_type")
        schedule = self._ranking_execution.next_schedule(query_type)
        self._ranking_timer.start(schedule.delay_ms)
        # 현재 진행 중인 HTTP 요청은 강제로 끊지 않는다. 다음 보완 요청만
        # 막을 수 있도록 기준 시각 2.5초 전에 준비를 시작한다.
        self._ranking_preparation_timer.start(schedule.preparation_delay_ms)
        logger.info(
            "다음 순위 조회 예약: %s + 0.25초 (기준 %s)",
            schedule.next_time.strftime("%H:%M:%S"),
            query_type,
        )

    def _ranking_now(self) -> datetime:
        provider = getattr(self._ranking_loader, "server_now", None)
        value = provider() if callable(provider) else None
        return value if isinstance(value, datetime) else datetime.now()

    def _update_clock_label(self) -> None:
        self._schedule_investor_backfill_if_due()
        visible = self._settings.get("show_server_clock") == "1"
        self._clock_label.setVisible(visible)
        if visible:
            self._clock_label.setText(self._ranking_now().strftime("%H:%M:%S"))

    def _schedule_investor_backfill_if_due(self) -> None:
        writer = self._entry_snapshot_writer
        if writer is None or not writer.isRunning():
            return
        now = self._ranking_now()
        targets: tuple[date, ...] = ()
        if now.weekday() >= 5:
            # 주말에는 직전 영업일의 미확정/누락 스냅샷을 언제 실행해도 보완한다.
            targets = tuple(now.date() - timedelta(days=offset) for offset in range(1, 5))
        elif now.time() >= clock_time(20, 5):
            # 과거 버전에서 장중 임시값을 확정값으로 저장한 기록도 함께 고친다.
            targets = tuple(now.date() - timedelta(days=offset) for offset in range(0, 7))
        elif now.time() < clock_time(7, 55):
            # 장 마감 뒤 앱을 켜지 않은 경우 주말까지 포함해 최근 날짜를 확인한다.
            targets = tuple(now.date() - timedelta(days=offset) for offset in range(1, 5))
        for target in targets:
            if target in self._investor_backfill_days:
                continue
            self._investor_backfill_days.add(target)
            writer.enqueue_investor_backfill(target, ())

    def _refresh_rankings(self) -> None:
        if self._closing or self._ranking_loader is None:
            return
        if self._ranking_worker_controller.is_running:
            return
        self._refresh_button.setEnabled(False)
        self._set_api_status("API: 연결 중…", "#B36B00")
        self.statusBar().showMessage("순위와 신고가를 조회하는 중입니다…")
        logger.info("순위 조회 작업 시작: %s", self._ranking_now().strftime("%H:%M:%S"))
        self._ranking_worker_controller.start(self._ranking_loader)

    def _on_ranking_worker_finished(self) -> None:
        self._refresh_button.setEnabled(True)
        if self._closing or not self._ranking_execution.worker_finished():
            return
        logger.info("밀린 순위 조회를 즉시 시작합니다.")
        QTimer.singleShot(0, self._refresh_rankings)

    def _on_ranking_failed(self, message: str) -> None:
        self._ranking_execution.end_priority_preparation()
        logger.warning("순위 조회에 실패했습니다: %s", message)
        self._set_api_status("API: 오류", "#C00000")
        self.statusBar().showMessage("조회에 실패했습니다. 네트워크와 API 설정을 확인하세요.")
        self._schedule_next_ranking_refresh()

    def _on_ranking_loaded(self, stocks: object) -> None:
        if not isinstance(stocks, tuple):
            self._on_ranking_failed("순위 응답 형식이 올바르지 않습니다.")
            return
        expected_count = int(getattr(self._ranking_loader, "EXPECTED_STOCKS", 0))
        outcome = self._ranking_execution.handle_response(
            stocks,
            expected_count=expected_count,
            has_blocking_modal=self._has_blocking_modal(),
        )
        decision = outcome.decision
        if decision.action in {RankingResponseAction.RETRY_SOON, RankingResponseAction.WAIT_NEXT}:
            self._set_api_status("API: 재조회", "#B36B00")
            self.statusBar().showMessage(f"순위 응답이 {len(stocks)}/{expected_count}개입니다. 기존 목록을 유지하고 다시 조회합니다…")
            if decision.action == RankingResponseAction.RETRY_SOON:
                QTimer.singleShot(1_500, self._refresh_rankings)
            else:
                self._schedule_next_ranking_refresh()
            return
        # 설정·테마·입력 창을 조작하는 중에는 표 전체를 다시 만들지 않는다.
        # 최신 결과 하나만 보관하고 창이 닫힌 뒤 반영해 입력 끊김을 막는다.
        if decision.action == RankingResponseAction.DEFER_WHILE_MODAL:
            self._set_connected_api_status()
            self._schedule_next_ranking_refresh()
            self._schedule_deferred_ranking_flush()
            return
        change_summary = outcome.change_summary
        if change_summary is None:
            self._on_ranking_failed("순위 변경 계산 결과가 없습니다.")
            return
        self._rank_changed_codes = set(change_summary.changed_codes)
        if self._rank_changed_codes:
            changed_names = ", ".join(
                str(getattr(stock, "name", getattr(stock, "code", "")))
                for stock in stocks
                if str(getattr(stock, "code", "")) in self._rank_changed_codes
            )
            logger.info("실제 순위 변동 감지: %s개 · %s", len(self._rank_changed_codes), changed_names)
        unchanged = change_summary.unchanged
        logger.info(
            "순위 조회 완료: %s · %s개 · %s",
            self._ranking_now().strftime("%H:%M:%S"),
            len(stocks),
            "이전 순위와 동일" if unchanged else "순위 변동 반영",
        )

        self._table_stack.setCurrentWidget(self._table)
        self._table.setRowCount(len(stocks))
        # 새 행과 테마 배지를 만들기 전에 반응형 행 높이·글자 크기를 먼저
        # 확정한다. 행을 다 만든 뒤 다시 계산하면 한 번의 순위 갱신에서
        # 배지가 두 번 생성되어 표 크기가 흔들리는 것처럼 보인다.
        self._apply_table_visuals(refresh_theme_badges=False)
        self._set_connected_api_status()
        self._row_by_code.clear()
        self._ranked_stock_names.clear()
        self._visible_theme_frequency = visible_theme_frequency(stocks, self._themes)
        codes = tuple(stock.code for stock in stocks)
        self._prepare_top20_trade_value_index(codes, self._ranking_now())
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_markets"):
            self._stock_markets.update(self._stock_lookup.load_markets(self._top20_realtime_codes(codes)))
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_last_prices"):
            self._current_prices.update(self._stock_lookup.load_last_prices(codes))
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_intraday_highs"):
            self._today_high_prices.update(self._stock_lookup.load_intraday_highs(codes, self._ranking_now().date()))
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_fundamentals"):
            self._fundamentals.update(self._stock_lookup.load_fundamentals(codes))
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_nxt_enabled"):
            saved_nxt = self._stock_lookup.load_nxt_enabled(codes, self._ranking_now().strftime("%Y-%m-%d"))
            self._nxt_checked_codes.update(saved_nxt)
            self._nxt_enabled_codes.update(code for code, enabled in saved_nxt.items() if enabled)
            self._nxt_enabled_codes.difference_update(code for code, enabled in saved_nxt.items() if not enabled)
        use_ranking_price = self._is_after_hours_data_pause()
        for row, stock in enumerate(stocks):
            self._row_by_code[stock.code] = row
            self._ranked_stock_names[stock.code] = stock.name
            try:
                self._last_change_rates[stock.code] = float(stock.change_rate)
            except (TypeError, ValueError):
                pass
            self._new_high_periods[stock.code] = frozenset(getattr(stock, "new_high_periods", ()))
            ranking_price = getattr(stock, "current_price", None)
            if use_ranking_price and isinstance(ranking_price, int) and ranking_price > 0:
                self._current_prices[stock.code] = ranking_price
                self._pending_price_cache[stock.code] = ranking_price
            values = (
                str(stock.rank),
                stock.name,
                "",
                stock.change_rate,
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
            )
            for column, value in enumerate(values):
                item = self._change_rate_item(value) if column == 3 else QTableWidgetItem(value)
                item.setBackground(self._row_background_color(stock.code, row))
                self._table.setItem(row, column, item)
            self._table.item(row, 1).setData(Qt.ItemDataRole.UserRole, stock.code)
            self._table.item(row, 1).setData(Qt.ItemDataRole.UserRole + 2, stock.code in self._nxt_enabled_codes)
            if stock.code in self._nxt_enabled_codes:
                self._table.item(row, 1).setToolTip("NXT 거래 가능")
            self._table.setCellWidget(row, 2, self._theme_badges(stock.code, stock.name))
        if self._pending_price_cache and not self._price_cache_timer.isActive():
            self._price_cache_timer.start()
        for stock in stocks:
            current_price = self._current_prices.get(stock.code)
            row = self._row_by_code[stock.code]
            if current_price is not None:
                self._render_current_price(stock.code)
            self._render_trade_values(stock.code)
            self._render_market_cap(stock.code)
            self._render_new_high_price(stock.code)
            self._render_high_distance(stock.code)
        for code in self._row_by_code:
            self._apply_row_background(code)
        if bool(getattr(self, "_theme_group_sort_enabled", False)):
            self._sort_visible_rows_by_theme_group(True)
        self._ensure_initial_ranking_rows_visible()
        self._start_rank_changed_highlights()
        self.statusBar().showMessage(f"조회 완료 · {len(stocks)}개 종목 · {'순위 변동 없음' if unchanged else '순위 변동 반영'} · 실시간 체결 데이터 연결 중")
        self._schedule_theme_trade_summary()
        # NXT 시간에는 NXT 불가 종목을 함께 ``_NX``로 등록하면 키움이
        # WebSocket 연결 전체를 종료할 수 있다. 캐시된 가능 여부는 즉시
        # 사용하고, 아직 확인되지 않은 종목만 먼저 ka10100으로 확인한 뒤
        # NXT 가능 종목만 구독한다.
        if self._is_nxt_only_session() and self._start_nxt_eligibility_loading(codes):
            self.statusBar().showMessage(f"조회 완료 · {len(stocks)}개 종목 · NXT 가능 종목을 확인하는 중입니다…")
        else:
            self._start_realtime_subscription(self._top20_realtime_codes(codes))
            # 정상 구독 직후에는 subscription_ready가 후속 보완을 시작한다.
            # 연결이 아직 준비되지 않은 경우만을 위한 안전장치다.
            QTimer.singleShot(5_000, lambda: self._start_realtime_followups(codes))
        self._schedule_next_ranking_refresh()
        # 분봉·기본정보 40건 동시 보완은 모의 API 제한을 쉽게 초과하므로,
        # 안정적인 순위 조회가 확인된 뒤 사용자가 따로 실행하는 방식으로 제공한다.

    def _schedule_deferred_ranking_flush(self) -> None:
        if self._deferred_ranking_flush_scheduled:
            return
        self._deferred_ranking_flush_scheduled = True
        QTimer.singleShot(150, self._flush_deferred_ranking)

    def _flush_deferred_ranking(self) -> None:
        self._deferred_ranking_flush_scheduled = False
        if self._closing or not self._ranking_execution.has_deferred_response:
            return
        if self._has_blocking_modal():
            self._schedule_deferred_ranking_flush()
            return
        stocks = self._ranking_execution.take_deferred_response()
        if stocks is not None:
            self._on_ranking_loaded(stocks)

    def _defer_table_update_while_modal(self) -> bool:
        """설정/입력 창을 조작하는 동안에는 메인 표 렌더링을 미룬다."""
        if not self._has_blocking_modal():
            return False
        self._table_update_deferred = True
        if not self._table_update_flush_scheduled:
            self._table_update_flush_scheduled = True
            QTimer.singleShot(150, self._flush_deferred_table_updates)
        return True

    def _flush_deferred_table_updates(self) -> None:
        self._table_update_flush_scheduled = False
        if self._closing or not self._table_update_deferred:
            return
        if self._has_blocking_modal():
            self._defer_table_update_while_modal()
            return
        self._table_update_deferred = False
        for code in tuple(self._row_by_code):
            current_price = self._current_prices.get(code)
            if current_price is not None:
                row = self._row_by_code[code]
                self._render_current_price(code)
                self._set_near_high_level(code, current_price, play_sound=False)
            self._render_new_high_price(code)
            self._render_high_distance(code)
            self._render_trade_values(code)
            self._render_market_cap(code)
            self._apply_near_high_background(code)
        self._table.viewport().update()

    def _has_blocking_modal(self) -> bool:
        """Image-theme review is allowed to coexist with the live ranking table."""
        return QApplication.activeModalWidget() is not None and not bool(
            getattr(self, "_image_theme_workflow_active", False)
        )

    def _theme_badges(self, code: str, name: str) -> QWidget:
        widget = QWidget(); layout = QHBoxLayout(widget); layout.setContentsMargins(2, 2, 2, 2); layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        themes = [(index, theme.strip()) for index, theme in enumerate(self._themes.get("".join(name.split()), "").split(",")) if theme.strip()]
        frequency = getattr(self, "_visible_theme_frequency", {})
        themes.sort(key=lambda item: (-frequency.get(item[1].casefold(), 0), item[0]))
        if self._settings.get("theme_badge_enabled") != "1":
            label = QLabel(", ".join(theme for _, theme in themes) or "-")
            label.setStyleSheet("padding: 2px;")
            layout.addWidget(label)
            layout.addStretch()
            return widget
        for _, theme in themes:
            if theme:
                color = self._theme_store.color_for_stock_theme(code, theme) if self._theme_store else "#DCE6F1"
                badge_size = int(self._settings.get("theme_badge_font_size"))
                padding = int(self._settings.get("theme_badge_padding"))
                # 0(자동)이면 테마 배지도 표 글자 크기를 따른다. 이전에는
                # 배지만 Qt 기본 크기에 남아 작은 화면에서 유독 커 보였다.
                if badge_size <= 0:
                    badge_size = max(4, self._table.font().pointSize())
                    # 사용자가 행 높이를 직접 키운 경우에는 배지도 행 안에서
                    # 자연스럽게 함께 커지게 한다. 자동 행 높이일 때는 표
                    # 글자 크기만 따라가므로 과도하게 커지지 않는다.
                    configured_row_height = int(self._settings.get("ui_row_height"))
                    if configured_row_height:
                        row = self._row_by_code.get(code, 0)
                        badge_size = max(badge_size, min(24, max(4, (self._table.rowHeight(row) - 8) // 2)))
                    padding = min(padding, max(0, (badge_size - 4) // 3))
                font_style = f"font-size:{badge_size}px;"
                badge = QPushButton(theme); badge.setStyleSheet(f"background:{color}; color:{text_color(color)}; border-radius:5px; padding:{padding}px {padding + 3}px; {font_style}")
                badge.clicked.connect(lambda _, value=theme, stock_code=code: self._edit_badge_color(stock_code, value))
                layout.addWidget(badge)
        layout.addStretch(); return widget

    def _new_high_label(self, code: str, label: str) -> str:
        period_text = self._settings.get("high_distance_period")
        if period_text == "historical":
            target = self._historical_high_prices.get(code)
            current = self._current_prices.get(code)
            return "신고가" if target is not None and current is not None and current >= target else "-"
        period = int(period_text)
        return "신고가" if code in self._today_high_codes or period in self._new_high_periods.get(code, frozenset()) else "-"

    def _render_new_high_price(self, code: str) -> None:
        row = self._row_by_code.get(code)
        if row is None:
            return
        price = self._selected_high_price(code)
        label = f"{price:,}" if price else "-"
        item = QTableWidgetItem(label)
        item.setBackground(self._row_background_color(code, row))
        self._table.setItem(row, 13, item)

    def _selected_high_price(self, code: str) -> int | None:
        return selected_high_price(
            self._settings.get("high_distance_period"),
            daily=self._daily_highs.get(code),
            fundamentals=self._fundamentals.get(code),
            historical_high=self._historical_high_prices.get(code),
            today_high=self._today_high_prices.get(code),
        )

    def _edit_badge_color(self, code: str, theme: str) -> None:
        if not self._theme_store: return
        dialog=ThemeColorDialog(theme, self._theme_store.color_for_stock_theme(code, theme), self)
        if dialog.exec():
            if dialog.stock_only: self._theme_store.set_stock_theme_color(code, theme, dialog.color)
            else: self._theme_store.set_color(theme, dialog.color)
            self._refresh_rankings()

    def _trade_display_mode(self, period: str) -> str:
        try:
            return self._settings.get(f"trade_display_{period}_mode")
        except KeyError:
            return "live"

    def _update_trade_display_headers(self) -> None:
        headers = {
            "1m": (4, 6, "1분강도", "1분"),
            "5m": (10, 7, "5분강도", "5분"),
            "60m": (11, 8, "60분강도", "60분"),
            "day": (12, 9, "1일강도", "1일"),
        }
        for period, (strength_column, trade_column, strength_label, trade_label) in headers.items():
            completed = self._trade_display_mode(period) == "completed"
            strength = self._table.horizontalHeaderItem(strength_column)
            trade_value = self._table.horizontalHeaderItem(trade_column)
            if strength is not None:
                strength.setText(f"{strength_label} (직전)" if completed else strength_label)
            if trade_value is not None:
                trade_value.setText(f"{trade_label} (직전)" if completed else trade_label)

    def _update_high_display_headers(self) -> None:
        period = self._settings.get("high_distance_period")
        labels = {"5": "신고가(5)", "20": "신고가(20)", "250": "신고가(250)", "historical": "신고가(역)"}
        distance_labels = {"5": "신고가%(5)", "20": "신고가%(20)", "250": "신고가%(250)", "historical": "신고가%(역)"}
        price_header = self._table.horizontalHeaderItem(13)
        distance_header = self._table.horizontalHeaderItem(14)
        if price_header is not None:
            price_header.setText(labels.get(period, labels["250"]))
        if distance_header is not None:
            distance_header.setText(distance_labels.get(period, distance_labels["250"]))

    def _toggle_table_header_mode(self, logical_index: int) -> None:
        if logical_index == 2:
            self._theme_group_sort_enabled = not bool(getattr(self, "_theme_group_sort_enabled", False))
            self._sort_visible_rows_by_theme_group(self._theme_group_sort_enabled)
            mode = "테마 수가 많은 순 · 테마 안에서는 등락률 순" if self._theme_group_sort_enabled else "실시간 순위 순"
            self.statusBar().showMessage(f"표 정렬: {mode}")
            return
        if logical_index == 13:
            periods = selected_high_cycle_periods(self._settings.get("high_header_cycle_periods"))
            current = self._settings.get("high_distance_period")
            try:
                next_period = periods[(periods.index(current) + 1) % len(periods)]
            except ValueError:
                next_period = periods[0]
            self._settings.set("high_distance_period", next_period)
            self._update_high_display_headers()
            if next_period == "historical":
                # 장외에는 불필요한 보완 조회를 쉬지만, 사용자가 역사적 기준을
                # 직접 선택한 경우에는 필요한 연봉 조회를 바로 시작한다.
                self._start_historical_high_loading(tuple(self._row_by_code))
            # 신고가 기준이 바뀌면 근접 단계에 따른 행 전체 배경도 달라진다.
            # 종목마다 중간 상태를 화면에 내보내면 거래대금 강조 배경이 잠깐
            # 지워졌다가 돌아와 깜빡여 보이므로, 화면 출력만 보류한 채 최종
            # 상태까지 만든 뒤 한 번에 표시한다. 실시간 수신·계산은 계속된다.
            self._table.setUpdatesEnabled(False)
            try:
                for code in self._row_by_code:
                    self._render_new_high_price(code)
                    self._render_high_distance(code)
                # 행 전체 배경 갱신이 덮은 거래대금 고유 강조를 마지막에
                # 복원한다. 강도 글자색·굵기·아이콘도 같은 최종 상태로 맞춘다.
                for code in self._row_by_code:
                    self._render_trade_values(code)
            finally:
                self._table.setUpdatesEnabled(True)
                self._table.viewport().update()
            self.statusBar().showMessage(f"신고가 기준: {self._table.horizontalHeaderItem(13).text()}")
            return
        self._toggle_trade_display_mode(logical_index)

    def _sort_visible_rows_by_theme_group(self, enabled: bool) -> None:
        """구독·계산 순서는 유지하고 현재 표의 행만 테마별로 재배열한다."""
        selected_code = self._selected_table_code
        frequency, trade_totals = self._theme_group_sort_metrics()
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 1)
            theme_item = self._table.item(row, 2)
            rank_item = self._table.item(row, 0)
            change_item = self._table.item(row, 3)
            if name_item is None or theme_item is None:
                continue
            code = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
            name = name_item.text()
            try:
                rank = int(rank_item.text()) if rank_item is not None else 9999
            except (TypeError, ValueError):
                rank = 9999
            try:
                change = float(str(change_item.text()).replace("%", "")) if change_item is not None else -9999.0
            except (TypeError, ValueError):
                change = -9999.0
            themes = [value.strip() for value in self._themes.get("".join(name.split()), "").split(",") if value.strip()]
            sort_key = theme_group_sort_key(
                enabled, tuple(themes), frequency, trade_totals, change, rank,
            )
            theme_item.setText(sort_key)
            theme_item.setData(Qt.ItemDataRole.UserRole, code)
        self._table.setSortingEnabled(True)
        self._table.sortItems(2, Qt.SortOrder.AscendingOrder)
        self._table.setSortingEnabled(False)
        self._row_by_code.clear()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item is not None:
                code = str(item.data(Qt.ItemDataRole.UserRole) or "")
                if code:
                    self._row_by_code[code] = row
        if selected_code in self._row_by_code:
            selected_row = self._row_by_code[selected_code]
            if self._selected_table_cell is not None:
                self._selected_table_cell = (selected_row, self._selected_table_cell[1])
            self._table.selectRow(selected_row)
        self._table.viewport().update()

    def _theme_group_sort_metrics(self) -> tuple[dict[str, int], dict[str, float]]:
        """선택한 기준의 테마 종목 수와 거래대금 합계를 반환한다."""
        basis = self._settings.get("theme_group_sort_basis")
        excluded_values = parse_themes(self._settings.get("theme_trade_summary_excluded_stocks"), ",/|;")
        excluded = {"".join(value.split()).casefold() for value in excluded_values} if basis == "excluded" else set()
        period = self._settings.get("theme_trade_summary_period")
        period = period if period in {"1m", "5m", "60m", "day"} else "day"
        minutes = {"1m": 1, "5m": 5, "60m": 60}
        now = self._ranking_now()
        entries: list[tuple[tuple[str, ...], float]] = []
        for code, name in self._ranked_stock_names.items():
            if code.casefold() in excluded or "".join(name.split()).casefold() in excluded:
                continue
            value = (
                self._minute_aggregator.today_trade_value_eok(code, now)
                if period == "day"
                else self._minute_aggregator.bucket_trade_value_eok(code, minutes[period], now)
            )
            entries.append((parse_themes(self._themes.get("".join(name.split()), ""), ","), value))
        return aggregate_theme_metrics(entries)

    def _toggle_trade_display_mode(self, logical_index: int) -> None:
        periods = {
            4: ("1m", "1분"), 6: ("1m", "1분"),
            7: ("5m", "5분"), 10: ("5m", "5분"),
            8: ("60m", "60분"), 11: ("60m", "60분"),
            9: ("day", "1일"), 12: ("day", "1일"),
        }
        target = periods.get(logical_index)
        if target is None:
            return
        period, label = target
        completed = self._trade_display_mode(period) != "completed"
        self._settings.set(f"trade_display_{period}_mode", "completed" if completed else "live")
        self._update_trade_display_headers()
        for code in self._row_by_code:
            self._render_trade_values(code)
        self.statusBar().showMessage(f"{label} 거래대금·{label}강도: " + (f"직전 완료 {label}" if completed else f"실시간 진행 중 {label}"))

    def _refresh_new_highs(self) -> None:
        self._start_new_high_refresh()

    def _start_new_high_refresh(self) -> None:
        if self._closing or self._ranking_loader is None or not hasattr(self._ranking_loader, "refresh_new_highs"):
            return
        if self._new_high_worker_controller.is_running:
            return
        self._new_high_button.setEnabled(False)
        self.statusBar().showMessage("신고가 목록을 갱신하는 중입니다…")
        if not self._new_high_worker_controller.start(self._ranking_loader):
            self._new_high_button.setEnabled(True)

    def _on_new_high_refresh_completed(self) -> None:
        self.statusBar().showMessage("신고가 목록 갱신 완료")
        # 신고가 갱신 완료가 임의 시각의 순위 재조회로 이어지면 00/30초
        # 순위 스냅샷 흐름이 섞인다. 현재 표의 신고가 정보만 갱신하고,
        # 다음 순위 회차는 순위 타이머가 전담한다.
        for code in self._row_by_code:
            self._render_new_high_price(code)
            self._render_high_distance(code)
        if self._initial_nxt_codes:
            codes, self._initial_nxt_codes = self._initial_nxt_codes, ()
            self._start_nxt_phase(codes)

    def _on_new_high_refresh_failed(self, message: str) -> None:
        logger.warning("신고가 갱신에 실패했습니다: %s", message)
        self.statusBar().showMessage("신고가 갱신에 실패했습니다. 잠시 후 다시 시도하세요.")
        if self._initial_nxt_codes:
            codes, self._initial_nxt_codes = self._initial_nxt_codes, ()
            self._start_nxt_phase(codes)

    def _start_realtime_subscription(self, codes: tuple[str, ...]) -> None:
        worker_exists = self._realtime_worker is not None
        worker_running = worker_exists and self._realtime_worker.isRunning()
        decision = self._realtime_subscription.plan(
            codes,
            self._nxt_enabled_codes,
            closing=self._closing,
            worker_available=self._realtime_worker_controller.available,
            worker_exists=worker_exists,
            worker_running=worker_running,
        )
        if decision.action == RealtimeSubscriptionAction.NONE:
            return
        if decision.action == RealtimeSubscriptionAction.STOP:
            self._realtime_worker_controller.stop()
            self._realtime_subscription.commit(decision)
            self.statusBar().showMessage("현재 시간에는 수신 가능한 실시간 체결 종목이 없습니다.")
            return
        if decision.action == RealtimeSubscriptionAction.UPDATE:
            self._realtime_worker_controller.update_codes(
                decision.active_codes,
                decision.nxt_codes,
            )
            self._realtime_subscription.commit(decision)
            self.statusBar().showMessage(f"실시간 연결 유지 · 구독 종목 변경 중 · {len(decision.active_codes)}종목")
            return
        if decision.stop_existing_first:
            if not self._realtime_worker_controller.stop():
                self._on_background_failure("이전 실시간 연결을 아직 종료하는 중입니다. 잠시 후 다시 시도합니다.")
                return
        self._realtime_diagnostics["worker_abnormal_disconnects"] = 0
        self._realtime_diagnostics["worker_reconnects"] = 0
        if not self._realtime_worker_controller.start(
            decision.active_codes,
            decision.nxt_codes,
            followup_codes=codes,
        ):
            self._on_background_failure("실시간 연결 작업을 시작하지 못했습니다.")
            return
        self._realtime_subscription.commit(decision)

    def _is_nxt_only_session(self) -> bool:
        """실전 NXT 시간대인지 판단한다.

        모의투자에는 NXT WebSocket이 없으므로, 그 환경에서는 기존 KRX
        연결 흐름을 유지한다.
        """
        return is_nxt_only_session(str(self._environment_selector.currentData()), self._ranking_now())

    def _schedule_realtime_session_refresh(self) -> None:
        if self._closing:
            return
        now = self._ranking_now()
        next_boundary = next_realtime_session_boundary(now)
        self._realtime_session_timer.start(max(100, round((next_boundary - now).total_seconds() * 1000)))

    def _on_realtime_session_boundary(self) -> None:
        if self._closing:
            return
        self._start_realtime_subscription(tuple(self._row_by_code))
        self._schedule_realtime_session_refresh()

    def _start_realtime_followups(self, codes: tuple[str, ...]) -> None:
        """실시간 순위 뒤의 저우선순위 보완 조회를 시작한다."""
        if self._closing or self._ranking_execution.priority_preparing:
            return
        self._start_secondary_loading(codes)

    def _ensure_today_minute_bar_storage(self, now: datetime) -> None:
        """날짜가 바뀌면 메모리를 비우고, DB에서는 30일보다 오래된 분봉만 정리한다."""
        today = now.date()
        if self._minute_bar_storage_date == today:
            return
        self._flush_pending_minute_bars()
        self._minute_aggregator.discard_before(today)
        self._minute_history_codes.clear()
        if self._minute_bar_repository is not None:
            try:
                self._minute_bar_repository.purge_before(today - timedelta(days=30))
            except Exception as error:
                logger.warning("오래된 분봉 DB 정리 실패: %s", error)
        self._minute_bar_storage_date = today

    def _flush_pending_minute_bars(self) -> None:
        """실시간 체결을 1초 단위로 묶어 SQLite에 저장한다."""
        if not self._pending_minute_bars and not self._pending_market_index_bars:
            return
        if self._minute_bar_repository is None:
            return
        pending, self._pending_minute_bars = self._pending_minute_bars, {}
        market_pending, self._pending_market_index_bars = self._pending_market_index_bars, {}
        grouped: dict[str, list[MinuteOhlcv]] = {}
        for (code, _), bar in pending.items():
            grouped.setdefault(code, []).append(bar)
        try:
            self._minute_bar_repository.upsert_many(
                {code: tuple(bars) for code, bars in grouped.items()}
            )
            self._minute_bar_repository.upsert_market_index_minutes(market_pending)
            self._start_top20_market_repair()
        except Exception as error:
            # 저장 호출 중에는 GUI 이벤트가 처리되지 않지만, 향후 구현이
            # 비동기로 바뀌어도 새 값이 우선하도록 현재 대기분 뒤에 병합한다.
            self._pending_minute_bars = {**pending, **self._pending_minute_bars}
            self._pending_market_index_bars = {
                **market_pending, **self._pending_market_index_bars,
            }
            logger.warning("실시간 분봉 DB 저장 실패: %s", error)

    def _flush_daily_trade_comparisons(self) -> None:
        """이미 받은 ka10081 일봉값을 재사용해 개발 확인용 CSV를 갱신한다."""
        pending, self._pending_daily_trade_comparisons = self._pending_daily_trade_comparisons, {}
        if not pending or self._minute_bar_repository is None:
            return
        try:
            count = self._minute_bar_repository.update_comparison_reports(pending, self._ranking_now().date())
            if count:
                logger.info("분봉·일봉 거래대금 비교 CSV 갱신: %s건", count)
        except Exception as error:
            logger.warning("분봉·일봉 거래대금 비교 CSV 저장 실패: %s", error)

    def _on_trade_tick(self, tick: TradeTick) -> None:
        # 서버의 별도 '구독 완료' 통지가 누락되더라도 실제 체결이 한 건
        # 도착했다면 중앙 실시간 경로가 정상이라는 가장 확실한 증거다.
        if self._active_api_route == "central_waiting":
            self._active_api_route = "central"
            self._set_connected_api_status()
        row = self._row_by_code.get(tick.code)
        # 현재 표에서는 빠졌어도 현재·다음 30초 지수 구성 종목이면 집계를
        # 계속한다. UI 갱신만 생략하고 0B 분봉 누적은 유지한다.
        if row is None and tick.code not in self._top20_collector.active_codes:
            return
        if tick.market_cap_eok is not None and tick.market_cap_eok > 0:
            self._realtime_market_caps[tick.code] = float(tick.market_cap_eok)
        observed_at = self._ranking_now()
        if tick.change_rate is not None:
            self._last_change_rates[tick.code] = tick.change_rate
        if tick.execution_strength is not None:
            self._latest_execution_strength[tick.code] = tick.execution_strength
        if tick.session_type:
            self._latest_session_type[tick.code] = tick.session_type
        if tick.trade_volume:
            pressure = self._realtime_pressure.setdefault(tick.code, deque())
            pressure.append((observed_at, tick.trade_volume))
            cutoff = observed_at - timedelta(seconds=60)
            while pressure and pressure[0][0] < cutoff:
                pressure.popleft()
        if tick.current_price is not None:
            self._current_prices[tick.code] = tick.current_price
            self._pending_price_cache[tick.code] = tick.current_price
            if not self._price_cache_timer.isActive():
                self._price_cache_timer.start()
            if tick.high_price and tick.high_price > 0:
                previous_high = self._today_high_prices.get(tick.code, 0)
                if tick.high_price > previous_high:
                    self._today_high_prices[tick.code] = tick.high_price
                    self._pending_today_high_cache[tick.code] = tick.high_price
                if tick.current_price >= tick.high_price:
                    self._today_high_codes.add(tick.code)
            self._ensure_today_minute_bar_storage(observed_at)
            bar = self._minute_aggregator.ingest(tick, observed_at)
            if bar is not None:
                self._pending_minute_bars[(tick.code, bar.minute)] = bar
                if not self._minute_bar_save_timer.isActive():
                    self._minute_bar_save_timer.start()
        if not hasattr(self, "_pending_trade_ticks"):
            self._pending_trade_ticks: dict[str, TradeTick] = {}
            self._trade_tick_flush_timer = QTimer(self)
            self._trade_tick_flush_timer.setSingleShot(True)
            self._trade_tick_flush_timer.setInterval(120)
            self._trade_tick_flush_timer.timeout.connect(self._flush_trade_tick_updates)
        self._pending_trade_ticks[tick.code] = tick
        if not self._trade_tick_flush_timer.isActive():
            self._trade_tick_flush_timer.start()

    def _prepare_top20_trade_value_index(self, codes: tuple[str, ...], observed_at: datetime) -> None:
        """현재 순위 구성을 다음 30초 경계부터 사용할 대상으로 예약한다."""
        self._top20_collector.prepare(
            codes, observed_at,
            enabled=self._settings.get("rank_query_type") == "5",
            collection_open=self._top20_collection_open(observed_at),
        )

    @staticmethod
    def _top20_collection_open(moment: datetime) -> bool:
        return top20_collection_open(moment)

    def _top20_realtime_codes(self, visible_codes: tuple[str, ...]) -> tuple[str, ...]:
        """표의 최신 20개와 지수에 필요한 고정 구성만 합쳐 구독한다."""
        return self._top20_collector.realtime_codes(
            visible_codes, enabled=self._settings.get("rank_query_type") == "5",
        )

    def _update_top20_trade_value_index(self) -> None:
        now = self._ranking_now()
        nas_interrupted = self._active_api_route in {
            "central_waiting", "central_retry", "local_fallback",
        }
        update = self._top20_collector.advance(
            now,
            enabled=self._settings.get("rank_query_type") == "5",
            # NAS 원본이 끊긴 동안 로컬 페일오버 체결로 중앙 수집 기록을
            # 메우면 NAS DB가 연속 수집된 것처럼 보인다. 해당 구간은 비워
            # 기존 차트의 '수집 중단' 경계로 명확히 표시한다.
            collection_open=top20_collection_available(now, self._active_api_route),
            value_provider=self._top20_trade_value,
            market_provider=self._top20_market,
        )
        if update.completed is not None:
            self._save_top20_record(update.completed)
        if update.cohort_changed and self._row_by_code:
            self._start_realtime_subscription(self._top20_realtime_codes(tuple(self._row_by_code)))
        completed_rows = list(self._top20_collector.completed)
        if not update.collection_open:
            if self._top20_view_date == now.date() and self._top20_view_mode == "minute":
                self._top20_trade_value_chart.set_data(
                    None, (0.0, 0.0, 0.0), completed_rows,
                    "NAS 실시간 원본 연결이 끊겨 해당 구간을 기록하지 않습니다."
                    if nas_interrupted else
                    "TOP20 거래대금 수집 시간은 평일 08:00~19:59입니다.",
                )
            return
        if self._top20_view_date == now.date() and self._top20_view_mode in {"5m", "60m"}:
            interval = 5 if self._top20_view_mode == "5m" else 60
            completed = self._aggregate_top20_rows(completed_rows, interval)
            minute = update.live_minute
            bucket = minute.replace(minute=(minute.minute // interval) * interval, second=0, microsecond=0)
            current = update.live_values or (0.0, 0.0, 0.0)
            if completed and completed[-1][0] == bucket:
                previous = completed.pop()
                current = tuple(current[index] + previous[index + 1] for index in range(3))
            self._top20_trade_value_chart.set_data(
                bucket, current, completed,
                f"{interval}분봉 진행 중 · 앱 실행 구간의 1분 거래대금을 합산합니다.",
            )
            return
        if self._top20_view_date != now.date() or self._top20_view_mode != "minute":
            return
        if self._settings.get("rank_query_type") != "5":
            self._top20_trade_value_chart.set_data(
                None, (0.0, 0.0, 0.0), completed_rows,
                "순위 기준을 30초 간격으로 선택하면 집계를 시작합니다.",
            )
            return
        if not self._top20_collector.active_codes:
            self._top20_trade_value_chart.set_data(
                None, (0.0, 0.0, 0.0), completed_rows,
                "순위를 확보한 다음 30초 경계부터 집계를 시작합니다.",
            )
            return
        kospi, kosdaq, unknown = update.live_values or (0.0, 0.0, 0.0)
        self._top20_trade_value_chart.set_data(
            update.live_minute, (kospi, kosdaq, unknown), completed_rows,
            f"현재 30초 구성 {len(self._top20_collector.active_codes)}종목 · 두 구간을 1분으로 합산합니다.",
        )

    def _top20_trade_value(self, code: str, minute: datetime) -> float:
        return self._minute_aggregator.bucket_trade_value_eok(code, 1, minute)

    def _top20_market(self, code: str) -> str:
        return self._stock_markets.get(code, "")

    def _save_top20_record(self, record: Top20MinuteRecord) -> None:
        self._save_top20_trade_value_index(
            record.minute, record.total, record.codes, record.capture_state,
            record.market_values, record.market_counts, record.cohort_segments,
        )

    def _top20_market_counts(self, codes: tuple[str, ...]) -> tuple[int, int, int]:
        return self._top20_collector.market_counts(codes, self._top20_market)

    def _show_top20_trade_value_window(self) -> None:
        window = self._top20_trade_value_window
        window.show()
        window.raise_()
        window.activateWindow()

    def _show_top20_trade_value_date(self, selected_date: date) -> None:
        self._top20_view_date = selected_date
        if self._minute_bar_repository is None:
            rows = ()
        else:
            try:
                rows = self._minute_bar_repository.load_top20_trade_value_index_for_date(selected_date)
            except Exception as error:
                logger.warning("TOP20 과거 기록 조회 실패: %s", error); rows = ()
        completed = [(minute, kospi, kosdaq, unknown) for minute, _, _, _, kospi, kosdaq, unknown in rows]
        interval = 5 if self._top20_view_mode == "5m" else 60 if self._top20_view_mode == "60m" else 1
        if interval > 1:
            completed = self._aggregate_top20_rows(completed, interval)
        status = f"{selected_date:%Y-%m-%d} · 저장 기록 {len(completed)}개" if completed else f"{selected_date:%Y-%m-%d} · 저장된 기록이 없습니다."
        self._top20_trade_value_chart.set_data(None, (0.0, 0.0, 0.0), completed, status)

    def _show_top20_trade_value_mode(self, mode: str) -> None:
        self._top20_view_mode = mode if mode in {"minute", "5m", "60m", "daily"} else "minute"
        self._top20_trade_value_chart.set_display_mode(self._top20_view_mode)
        if self._top20_view_mode in {"minute", "5m", "60m"}:
            self._show_top20_trade_value_date(self._top20_view_date)
            return
        if self._minute_bar_repository is None:
            rows = ()
        else:
            try:
                rows = self._minute_bar_repository.load_top20_daily_trade_values(365)
            except Exception as error:
                logger.warning("TOP20 일봉 기록 조회 실패: %s", error); rows = ()
        completed = [
            (datetime.combine(day, clock_time()), kospi, kosdaq, unknown)
            for day, kospi, kosdaq, unknown in rows
        ]
        status = f"일봉 {len(completed)}개 · KRX 정규장 09:00~15:29 합계" if completed else "저장된 일봉 기록이 없습니다."
        self._top20_trade_value_chart.set_data(None, (0.0, 0.0, 0.0), completed, status)

    @staticmethod
    def _aggregate_top20_rows(
        rows: list[tuple[datetime, float, float, float]], interval_minutes: int,
    ) -> list[tuple[datetime, float, float, float]]:
        buckets: dict[datetime, list[float]] = {}
        interval = max(1, int(interval_minutes))
        for minute, kospi, kosdaq, unknown in rows:
            bucket = minute.replace(minute=(minute.minute // interval) * interval, second=0, microsecond=0)
            values = buckets.setdefault(bucket, [0.0, 0.0, 0.0])
            values[0] += kospi; values[1] += kosdaq; values[2] += unknown
        return [(minute, *values) for minute, values in sorted(buckets.items())]

    def _show_top20_trade_value_statistics(self, days: int) -> None:
        if self._minute_bar_repository is None:
            return
        dialog = QDialog(self._top20_trade_value_window)
        dialog.setWindowTitle("TOP20 지수 통계")
        dialog.resize(620, 520)
        layout = QVBoxLayout(dialog)
        controls = QHBoxLayout(); controls.addWidget(QLabel("기간"))
        period = QComboBox()
        for label, value in (("최근 1주", 7), ("최근 1개월", 30), ("최근 1년", 365)):
            period.addItem(label, value)
        period.setCurrentIndex(max(0, period.findData(days)))
        controls.addWidget(period); controls.addStretch(1); layout.addLayout(controls)
        content = QTextEdit(); content.setReadOnly(True)
        layout.addWidget(content)
        def refresh_statistics() -> None:
            selected_days = int(period.currentData())
            try:
                hourly, comparisons = self._minute_bar_repository.load_top20_statistics(selected_days)
                lines = [
                    f"최근 {selected_days}일 범위 · 앱이 실행된 구간만 수집되므로 전체 시장을 완전히 대표하지 않습니다.",
                    "", "[시간대별 TOP20 1분 평균 · 높은 순]",
                ]
                lines.extend(f"{hour}  {Top20TradeValueChart._amount(value)}  (수집일 {count}일)" for hour, value, count in hourly)
                lines.extend(("", "[정규장 09:00~15:29 · 일별 TOP20 / 코스피+코스닥]"))
                for day, top20, kospi, kosdaq in comparisons:
                    market_total = kospi + kosdaq
                    ratio = top20 / market_total * 100 if market_total > 0 else None
                    ratio_text = f"{ratio:.2f}%" if ratio is not None else "전체시장 자료 없음"
                    lines.append(f"{day.isoformat()}  TOP20 {Top20TradeValueChart._amount(top20)} / 전체 {Top20TradeValueChart._amount(market_total)} · {ratio_text}")
                if not hourly and not comparisons: lines.append("저장된 통계 자료가 없습니다.")
                content.setPlainText("\n".join(lines))
            except Exception as error:
                content.setPlainText(f"통계를 불러오지 못했습니다.\n{error}")
        period.currentIndexChanged.connect(refresh_statistics)
        refresh_statistics()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject); close.accepted.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _reload_top20_completed(self, selected_date: date | None = None) -> None:
        if self._minute_bar_repository is None:
            return
        target = selected_date or self._top20_view_date
        try:
            rows = self._minute_bar_repository.load_top20_trade_value_index_for_date(target)
        except Exception as error:
            logger.warning("TOP20 저장 기록 다시 읽기 실패: %s", error); return
        self._top20_collector.completed.clear()
        self._top20_collector.completed.extend(
            (minute, kospi, kosdaq, unknown)
            for minute, _, _, _, kospi, kosdaq, unknown in rows[-1_440:]
        )

    def _start_top20_market_repair(self) -> None:
        if self._closing or self._minute_bar_repository is None:
            return
        if self._top20_repair_worker_controller.is_running:
            return
        now = time.monotonic()
        if now - self._top20_repair_last_started < 30.0:
            return
        self._top20_repair_last_started = now
        self._top20_repair_worker_controller.start(self._minute_bar_repository)

    def _on_top20_market_repaired(self, repaired: int) -> None:
        if repaired <= 0:
            return
        if self._top20_view_date == self._ranking_now().date():
            self._reload_top20_completed(self._top20_view_date)
            self._update_top20_trade_value_index()
        else:
            self._show_top20_trade_value_date(self._top20_view_date)

    def _save_top20_trade_value_index(
        self, minute: datetime | None, value: float,
        codes: tuple[str, ...], capture_state: str,
        market_values: tuple[float, float, float] | None = None,
        market_counts: tuple[int, int, int] | None = None,
        cohort_segments: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> None:
        if minute is None or not codes or self._minute_bar_repository is None:
            return
        try:
            if market_values is None or market_counts is None:
                kospi, kosdaq, unknown, counts = self._top20_market_values(codes, minute)
                market_values = (kospi, kosdaq, unknown)
                market_counts = counts
            self._minute_bar_repository.upsert_top20_trade_value_index(
                minute, value, codes, capture_state,
                kospi_trade_value_eok=market_values[0],
                kosdaq_trade_value_eok=market_values[1],
                unknown_trade_value_eok=market_values[2],
                kospi_stock_count=market_counts[0],
                kosdaq_stock_count=market_counts[1],
                unknown_stock_count=market_counts[2],
                cohort_segments=cohort_segments,
            )
        except Exception as error:
            logger.warning("TOP20 거래대금 지수 저장 실패: %s", error)

    def _top20_market_values(
        self, codes: tuple[str, ...], minute: datetime,
    ) -> tuple[float, float, float, tuple[int, int, int]]:
        values = [0.0, 0.0, 0.0]
        counts = [0, 0, 0]
        for code in codes:
            market = self._stock_markets.get(code, "")
            index = 0 if ("KOSPI" in market or "코스피" in market or market in {"유가", "STK", "1"}) else (1 if ("KOSDAQ" in market or "코스닥" in market or market in {"KSQ", "2"}) else 2)
            values[index] += self._minute_aggregator.bucket_trade_value_eok(code, 1, minute)
            counts[index] += 1
        return values[0], values[1], values[2], (counts[0], counts[1], counts[2])

    def _on_order_execution(self, execution: OrderExecution) -> None:
        """계좌 체결 순간의 화면 문맥을 복기용 DB에 비동기로 보존한다."""
        writer = self._entry_snapshot_writer
        if writer is None or not writer.isRunning() or not execution.code:
            return
        now = self._ranking_now()
        executed_at = now
        raw_time = "".join(character for character in execution.trade_time if character.isdigit())
        if len(raw_time) >= 6:
            try:
                executed_at = now.replace(
                    hour=int(raw_time[:2]), minute=int(raw_time[2:4]),
                    second=int(raw_time[4:6]), microsecond=0,
                )
            except ValueError:
                pass
        name = execution.name or self._ranked_stock_names.get(execution.code, execution.code)
        normalized_name = "".join(name.split())
        themes = tuple(theme.strip() for theme in self._themes.get(normalized_name, "").split(",") if theme.strip())
        target = self._selected_high_price(execution.code)
        high_distance = max(0.0, (target - execution.price) / target * 100) if target else None
        broker_key = ":".join(filter(None, (execution.order_no, execution.execution_no)))
        execution_key = f"{executed_at.date().isoformat()}:{execution.code}:{broker_key}" if broker_key else ""
        if not execution_key:
            execution_key = hashlib.sha256(
                f"{execution.code}|{execution.side}|{executed_at.isoformat()}|{execution.price}|{execution.quantity}".encode()
            ).hexdigest()
        program = dict(self._latest_program_trade.get(execution.code, {}))
        investor_flow = {"program_trade": program} if program else {"program_trade": {"available": False, "backfill_pending": True}}
        writer.enqueue(TradeEntrySnapshot(
            execution_key=execution_key, order_no=execution.order_no,
            stock_code=execution.code, stock_name=name, side=execution.side,
            executed_at=executed_at, price=execution.price, quantity=execution.quantity,
            market=execution.market, rank=self._ranking_execution.rank_by_code.get(execution.code),
            trade_value_1m_eok=self._minute_aggregator.trade_value_eok(execution.code, 1),
            trade_value_5m_eok=self._minute_aggregator.trade_value_eok(execution.code, 5),
            themes=themes, theme_ranks=self._theme_ranks_at_entry(execution.code, themes),
            high_distance_percent=high_distance, orderbook=self._execution_pressure_at_entry(execution.code, now),
            investor_flow=investor_flow,
            market_state=self._entry_market_state(),
            capture_state="realtime_core",
        ))

    def _on_program_trade_tick(self, tick: object) -> None:
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import ProgramTradeTick
        if not isinstance(tick, ProgramTradeTick):
            return
        self._latest_program_trade[tick.code] = {
            "available": True, "source": "kiwoom_realtime_0w",
            "observed_at": self._ranking_now().isoformat(timespec="seconds"),
            "trade_time": tick.trade_time, "market": tick.market,
            "net_buy_quantity": tick.net_buy_quantity,
            "net_buy_quantity_change": tick.net_buy_quantity_change,
            "net_buy_amount_million_won": tick.net_buy_amount_million_won,
            "net_buy_amount_change_million_won": tick.net_buy_amount_change_million_won,
        }

    def _theme_ranks_at_entry(self, target_code: str, themes: tuple[str, ...]) -> dict[str, int]:
        result: dict[str, int] = {}
        for theme in themes:
            members = []
            for code, name in self._ranked_stock_names.items():
                stock_themes = tuple(value.strip().casefold() for value in self._themes.get("".join(name.split()), "").split(","))
                if theme.casefold() in stock_themes and code in self._ranking_execution.rank_by_code:
                    members.append((self._ranking_execution.rank_by_code[code], code))
            members.sort()
            for position, (_, code) in enumerate(members, 1):
                if code == target_code:
                    result[theme] = position
                    break
        return result

    def _entry_market_state(self) -> dict[str, object]:
        ordered = sorted(self._ranking_execution.rank_by_code.items(), key=lambda item: item[1])
        rates = [self._last_change_rates[code] for code, _ in ordered if code in self._last_change_rates]
        visible_state = {
            "visible_stock_count": len(ordered),
            "advancing_count": sum(rate > 0 for rate in rates),
            "declining_count": sum(rate < 0 for rate in rates),
            "flat_count": sum(rate == 0 for rate in rates),
            "average_change_rate": sum(rates) / len(rates) if rates else None,
            "visible_trade_value_1m_eok": sum(self._minute_aggregator.trade_value_eok(code, 1) for code, _ in ordered),
            "visible_trade_value_5m_eok": sum(self._minute_aggregator.trade_value_eok(code, 5) for code, _ in ordered),
            "top_stocks": [
                {"rank": rank, "code": code, "name": self._ranked_stock_names.get(code, code)}
                for code, rank in ordered[:10]
            ],
            "theme_frequency": dict(getattr(self, "_visible_theme_frequency", {})),
        }
        return {**self._latest_market_state, "ranking_table": visible_state}

    def _on_market_index_tick(self, tick: object) -> None:
        if not isinstance(tick, MarketIndexTick):
            return
        state = dict(self._latest_market_state)
        market = dict(state.get(tick.market, {}))
        updates = {
            "index": tick.index_value,
            "change_rate": tick.change_rate,
            "trade_value_million_won": tick.cumulative_trade_value_million_won,
            "trade_value_eok": (
                tick.cumulative_trade_value_million_won / 100
                if tick.cumulative_trade_value_million_won is not None else None
            ),
            "advancing_count": tick.advancing_count,
            "declining_count": tick.declining_count,
            "flat_count": tick.flat_count,
        }
        market.update({key: value for key, value in updates.items() if value is not None})
        state[tick.market] = market
        state["source"] = "kiwoom_realtime_0J_0U"
        state["observed_at"] = datetime.now().isoformat(timespec="seconds")
        state["trade_time"] = tick.trade_time
        self._latest_market_state = state
        if tick.index_value is not None:
            now = self._ranking_now()
            digits = "".join(character for character in str(tick.trade_time or "") if character.isdigit())
            clock = digits[-6:] if len(digits) >= 6 else digits
            if len(clock) >= 4:
                hour, minute = int(clock[:2]), int(clock[2:4])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    now = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            minute = now.replace(second=0, microsecond=0)
            key = (tick.market, minute); value = float(tick.index_value)
            trade_value = float(tick.cumulative_trade_value_million_won) / 100 if tick.cumulative_trade_value_million_won is not None else None
            previous = self._pending_market_index_bars.get(key)
            self._pending_market_index_bars[key] = (
                previous[0] if previous else value,
                max(previous[1], value) if previous else value,
                min(previous[2], value) if previous else value,
                value, trade_value,
            )
            if not self._minute_bar_save_timer.isActive():
                self._minute_bar_save_timer.start()

    def _on_realtime_diagnostics_changed(self, diagnostics: object) -> None:
        if not isinstance(diagnostics, dict):
            return
        # 작업 객체가 세션 경계에서 교체되어도 앱 실행 중 누적치는 유지한다.
        previous_disconnects = int(self._realtime_diagnostics.get("worker_abnormal_disconnects", 0))
        previous_reconnects = int(self._realtime_diagnostics.get("worker_reconnects", 0))
        current_disconnects = int(diagnostics.get("abnormal_disconnects", 0))
        current_reconnects = int(diagnostics.get("reconnects", 0))
        self._realtime_diagnostics["abnormal_disconnects"] = int(self._realtime_diagnostics.get("abnormal_disconnects", 0)) + max(0, current_disconnects - previous_disconnects)
        self._realtime_diagnostics["reconnects"] = int(self._realtime_diagnostics.get("reconnects", 0)) + max(0, current_reconnects - previous_reconnects)
        self._realtime_diagnostics["worker_abnormal_disconnects"] = current_disconnects
        self._realtime_diagnostics["worker_reconnects"] = current_reconnects
        self._realtime_diagnostics["last_disconnect_reason"] = diagnostics.get("last_disconnect_reason", "")
        self._realtime_diagnostics["updated_at"] = diagnostics.get("updated_at", "")

    def _execution_pressure_at_entry(self, code: str, now: datetime) -> dict[str, object]:
        pressure = self._realtime_pressure.get(code, deque())
        cutoff = now - timedelta(seconds=60)
        buy_volume = sum(volume for observed, volume in pressure if observed >= cutoff and volume > 0)
        sell_volume = sum(abs(volume) for observed, volume in pressure if observed >= cutoff and volume < 0)
        total = buy_volume + sell_volume
        return {
            "source": "0B_execution_flow", "window_seconds": 60,
            "execution_strength": self._latest_execution_strength.get(code),
            "buy_execution_volume": buy_volume, "sell_execution_volume": sell_volume,
            "buy_share_percent": buy_volume / total * 100 if total else None,
            "session_type": self._latest_session_type.get(code, ""),
        }

    def _flush_trade_tick_updates(self) -> None:
        pending = tuple(self._pending_trade_ticks.values())
        self._pending_trade_ticks.clear()
        if self._defer_table_update_while_modal():
            for tick in pending:
                if tick.current_price is not None:
                    self._set_near_high_level(tick.code, tick.current_price)
            return
        for tick in pending:
            row = self._row_by_code.get(tick.code)
            if row is None:
                continue
            if tick.market_cap_eok is not None and tick.market_cap_eok > 0:
                self._render_market_cap(tick.code)
            if tick.current_price is None:
                continue
            self._render_current_price(tick.code)
            if tick.change_rate is not None:
                self._table.setItem(row, 3, self._change_rate_item(f"{tick.change_rate:+.{self._decimal_places('change_rate')}f}%"))
            if tick.high_price and tick.high_price > 0:
                self._render_new_high_price(tick.code)
            self._set_near_high_level(tick.code, tick.current_price)
            self._apply_near_high_background(tick.code)
            self._render_high_distance(tick.code)
            self._render_trade_values(tick.code, live_only=tick.code not in self._minute_history_codes)

    def _save_current_price_cache(self) -> None:
        """체결마다 저장하지 않고 짧게 묶어 마지막 현재가만 보존한다."""
        if not self._pending_price_cache and not self._pending_today_high_cache:
            return
        prices = self._pending_price_cache
        highs = self._pending_today_high_cache
        self._pending_price_cache = {}
        self._pending_today_high_cache = {}
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_last_prices"):
            self._stock_lookup.update_last_prices(prices)
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_intraday_highs"):
            self._stock_lookup.update_intraday_highs(highs, self._ranking_now().date())

    def _start_secondary_loading(self, codes: tuple[str, ...]) -> None:
        """Start non-realtime API work in the defined priority order."""
        if self._ranking_execution.priority_preparing:
            return
        now = self._ranking_now()
        if now.weekday() < 5 and now.time() >= clock_time(20, 5) and self._journal_background_sync_day != now.date():
            self._journal_background_sync_day = now.date()
            self._ensure_journal_process()
            self._send_journal_command(action="sync")
        self._load_cached_daily_highs(codes)
        self._load_cached_historical_highs(codes)
        finalization_date, finalization = self._finalization_candidates(codes)
        if finalization:
            self._after_close_finalization_codes = set(finalization)
            self._after_close_finalization_date = finalization_date
            self._after_close_minute_received.clear()
            self._after_close_daily_received.clear()
        after_hours_pause = self._is_after_hours_data_pause()
        weekend_missing: tuple[str, ...] = ()
        if after_hours_pause:
            now = self._ranking_now()
            if now.weekday() >= 5 and self._daily_bar_repository is not None:
                # 금요일 아침에 받은 일봉도 날짜만 보면 최신이지만 장중 미완성
                # 값이다. 토·일을 합쳐 한 번도 확정 재조회하지 않은 종목만
                # 다시 받아 금요일 종가·고가로 교체한다.
                weekend_started = (now - timedelta(days=now.weekday() - 5)).date()
                finalized = self._daily_bar_repository.refreshed_since(codes, weekend_started)
                weekend_missing = tuple(code for code in codes if code not in finalized)
        started_phase = self._secondary_data_coordinator.start(
            codes,
            finalization_codes=finalization,
            after_hours_pause=after_hours_pause,
            weekend_daily_high_codes=weekend_missing,
        )
        if started_phase is SecondaryStartPhase.FINALIZATION and finalization_date is not None:
            for code in finalization:
                key = (finalization_date, code)
                self._finalization_attempts[key] = self._finalization_attempts.get(key, 0) + 1

    def _finalization_candidates(self, codes: tuple[str, ...]) -> tuple[date | None, tuple[str, ...]]:
        if self._after_close_finalization_codes or not codes or self._daily_bar_repository is None:
            return None, ()
        now = self._ranking_now()
        latest_trade_date = (
            self._daily_bar_repository.latest_trade_date_before(codes, now.date())
            if now.weekday() < 5 and now.hour * 60 + now.minute < 7 * 60 + 55
            else None
        )
        target = finalization_target_date(now, latest_trade_date)
        if target is None:
            return None, ()
        cached_nxt = (
            self._stock_lookup.load_cached_nxt_enabled(codes)
            if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_cached_nxt_enabled")
            else {}
        )
        finalized = self._daily_bar_repository.finalized_codes(codes, target)
        return target, finalization_candidates(
            codes, now, target, finalized, cached_nxt,
            self._finalization_attempts, self._finalization_retry_after,
        )

    def _is_after_hours_data_pause(self) -> bool:
        return after_hours_data_pause(self._ranking_now())

    def _start_daily_high_phase(self, codes: tuple[str, ...]) -> None:
        if self._ranking_execution.priority_preparing:
            return
        if not self._start_daily_high_loading(codes):
            self._start_fundamentals_phase(codes)

    def _start_fundamentals_phase(self, codes: tuple[str, ...]) -> None:
        if self._ranking_execution.priority_preparing:
            return
        if not self._start_fundamentals_loading(codes):
            self._start_initial_new_high_refresh(codes)

    def _start_nxt_phase(self, codes: tuple[str, ...]) -> None:
        if self._ranking_execution.priority_preparing:
            return
        if not self._start_nxt_eligibility_loading(codes):
            self._start_daily_krx_catalog_sync()

    def _start_initial_new_high_refresh(self, codes: tuple[str, ...]) -> None:
        if self._ranking_execution.priority_preparing:
            return
        if self._initial_new_high_refresh_started:
            if self._initial_nxt_codes:
                nxt_codes, self._initial_nxt_codes = self._initial_nxt_codes, ()
                self._start_nxt_phase(nxt_codes)
            return
        self._initial_new_high_refresh_started = True
        self._initial_nxt_codes = codes
        self._start_new_high_refresh()

    def _start_minute_history_loading(self, codes: tuple[str, ...], *, force: bool = False) -> bool:
        if self._closing or self._ranking_execution.priority_preparing or (self._is_after_hours_data_pause() and not force) or not self._minute_history_worker_controller.available:
            return False
        if self._minute_history_worker_controller.is_running:
            return True
        missing_codes = minute_history_candidates(codes, self._minute_history_codes, force=force)
        if not missing_codes:
            return False
        return self._minute_history_worker_controller.start(
            missing_codes,
            followup_codes=codes,
            forced=force,
        )

    def _on_history_received(self, code: str, bars: object) -> None:
        if not isinstance(bars, tuple):
            return
        now = self._ranking_now()
        self._ensure_today_minute_bar_storage(now)
        self._minute_aggregator.seed(code, bars, now)
        if code in self._after_close_finalization_codes and self._finalization_minute_bars_complete(code, bars):
            self._after_close_minute_received.add(code)
        same_day_highs = tuple(
            int(bar.high_price) for bar in bars
            if getattr(bar, "minute", None) is not None
            and bar.minute.date() == now.date()
            and getattr(bar, "high_price", 0) > 0
        )
        if same_day_highs:
            restored_high = max(same_day_highs)
            previous_high = self._today_high_prices.get(code, 0)
            if restored_high > previous_high:
                self._today_high_prices[code] = restored_high
                self._pending_today_high_cache[code] = restored_high
                if not self._price_cache_timer.isActive():
                    self._price_cache_timer.start()
        stored = self._minute_bar_repository is None
        if self._minute_bar_repository is not None:
            try:
                self._minute_bar_repository.upsert_bars(code, bars)
                self._minute_bar_repository.record_history_sync(code, now.date(), now, len(bars))
                stored = True
            except Exception as error:
                logger.warning("분봉 보완 DB 저장 실패 (%s): %s", code, error)
        if stored:
            self._minute_history_codes.add(code)
        if self._defer_table_update_while_modal():
            return
        self._apply_near_high_background(code)
        self._render_trade_values(code)

    def _start_nxt_eligibility_loading(self, codes: tuple[str, ...]) -> bool:
        if self._closing or self._ranking_execution.priority_preparing or not self._nxt_eligibility_worker_controller.available:
            return False
        if self._nxt_eligibility_worker_controller.is_running:
            return True
        cached: dict[str, bool] | None = None
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_nxt_enabled"):
            today = self._ranking_now().strftime("%Y-%m-%d")
            cached = self._stock_lookup.load_nxt_enabled(codes, today)
        missing = nxt_eligibility_candidates(codes, cached, self._nxt_checked_codes)
        if not missing:
            return False
        # NXT 가능 종목이 하나도 없더라도 후속 보완 작업이 멈추지 않게 한다.
        # 구독이 성공한 경우에는 subscription_ready에서도 다시 호출되지만,
        # 각 작업은 실행 중 여부를 확인하므로 중복 요청은 발생하지 않는다.
        return self._nxt_eligibility_worker_controller.start(missing)

    def _on_nxt_eligibility_received(self, code: str, enabled: bool) -> None:
        self._nxt_checked_codes.add(code)
        if enabled:
            self._nxt_enabled_codes.add(code)
        else:
            self._nxt_enabled_codes.discard(code)
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_nxt_enabled"):
            self._stock_lookup.update_nxt_enabled(code, enabled, self._ranking_now().strftime("%Y-%m-%d"))
        if self._defer_table_update_while_modal():
            return
        row = self._row_by_code.get(code)
        if row is None:
            return
        item = self._table.item(row, 1)
        if item is None:
            return
        item.setData(Qt.ItemDataRole.UserRole + 2, enabled)
        item.setToolTip("NXT 거래 가능" if enabled else "NXT 거래 불가")
        self._table.viewport().update()

    def _render_high_distance(self, code: str) -> None:
        row = self._row_by_code.get(code)
        current_price = self._current_prices.get(code)
        if row is None or current_price is None:
            return
        target = self._selected_high_price(code)
        if not target:
            return
        # 앱 시작·신고가 데이터 수신 뒤에도 현재가 기준의 근접 단계를 즉시
        # 계산한다. 설정 창을 열고 저장해야만 강조가 시작되던 문제를 막는다.
        self._set_near_high_level(code, current_price, play_sound=False)
        self._apply_near_high_background(code)
        distance = max(0.0, (target - current_price) / target * 100)
        level = self._near_high_levels.get(code, "")
        text = f"{distance:.{self._decimal_places('high_distance')}f}%"
        image = self._near_high_icon_image_path(level) if level and self._settings.get("near_high_show_icon") == "1" else None
        if level and image is None and self._settings.get("near_high_show_icon") == "1":
            text += f" {self._settings.get(f'near_high_icon_{level}')}"
        item = QTableWidgetItem(text)
        if image is not None:
            item.setIcon(QIcon(str(image)))
        item.setBackground(self._row_background_color(code, row))
        item.setForeground(QColor("#C00000") if level == "fire" else QColor("#C65911") if level == "caution" else QColor("#806000") if level == "interest" else QColor("black"))
        font = item.font()
        font.setBold(level == "fire")
        item.setFont(font)
        self._table.setItem(row, 14, item)

    def _near_high_level(self, code: str, current_price: int) -> str:
        if self._settings.get("near_high_alert_enabled") != "1":
            return ""
        target = self._selected_high_price(code)
        if target is None or target <= 0:
            return ""
        distance = max(0.0, (target - current_price) / target * 100)
        if distance <= float(self._settings.get("near_high_fire_percent")):
            return "fire"
        if distance <= float(self._settings.get("near_high_caution_percent")):
            return "caution"
        if distance <= float(self._settings.get("near_high_interest_percent")):
            return "interest"
        return ""

    def _set_near_high_level(self, code: str, current_price: int, *, play_sound: bool = True) -> None:
        previous = self._near_high_levels.get(code, "")
        level = self._near_high_level(code, current_price)
        severity = {"": 0, "interest": 1, "caution": 2, "fire": 3}
        selected_level = self._settings.get("near_high_row_alert_level")
        # 관심을 고르면 관심·주의·불, 주의를 고르면 주의·불 단계 모두 행을
        # 강조한다. 선택한 단계보다 더 신고가에 근접한 경우는 제외하지 않는다.
        if level and severity[level] >= severity.get(selected_level, 3):
            self._near_high_codes.add(code)
        else:
            self._near_high_codes.discard(code)
        if level:
            self._near_high_levels[code] = level
        else:
            self._near_high_levels.pop(code, None)
        # 신고가에 가까워지는 방향(관심 → 주의 → 불)으로만 알린다.
        # 멀어지는 방향(불 → 주의, 주의 → 관심, 관심 → 해제)에서는 재생하지 않는다.
        if play_sound and level and severity[level] > severity.get(previous, 0):
            self._play_near_high_sound_for_code(code, level)

    def _near_high_icon_image_path(self, level: str) -> Path | None:
        if not level:
            return None
        stored = self._settings.get(f"near_high_icon_{level}_image").strip()
        if not stored:
            return None
        path = self._api_config_path().parent.parent / stored
        return path if path.is_file() else None

    def _play_near_high_sound(self, level: str) -> None:
        if self._settings.get("near_high_sound_enabled") != "1":
            return
        stored = self._settings.get(f"near_high_sound_{level}").strip()
        path = self._api_config_path().parent.parent / stored if stored else None
        if path is None or not path.is_file():
            return
        player_pair = self._near_high_sound_players.get(level)
        if player_pair is None:
            output = QAudioOutput(self)
            output.setVolume(1.0)
            player = QMediaPlayer(self)
            player.setAudioOutput(output)
            self._near_high_sound_players[level] = (player, output)
        else:
            player, _ = player_pair
        player.setSource(QUrl.fromLocalFile(str(path)))
        player.play()

    def _play_near_high_sound_for_code(self, code: str, level: str) -> None:
        """Play an entered near-high level once per code/level during the cooldown."""
        key = (code, level)
        now = time.monotonic()
        last_played = self._near_high_sound_last_played.get(key)
        try:
            cooldown = max(0.0, float(self._settings.get("near_high_sound_cooldown_seconds")))
        except (TypeError, ValueError):
            cooldown = float(DEFAULT_SETTINGS["near_high_sound_cooldown_seconds"])
        if last_played is not None and now - last_played < cooldown:
            return
        self._near_high_sound_last_played[key] = now
        self._play_near_high_sound(level)

    def _start_daily_high_loading(self, codes: tuple[str, ...], *, force: bool = False) -> bool:
        if self._closing or self._ranking_execution.priority_preparing or not self._daily_high_worker_controller.available or self._daily_high_worker_controller.is_running:
            return self._daily_high_worker_controller.is_running
        today = self._ranking_now().date()
        if self._daily_high_cache_date != today:
            # 전날 저장값을 먼저 표시한 상태에서 그날의 ka10081 결과로 교체한다.
            # 여기서 비우면 앱 시작 때 직전 1일 값이 다시 빈칸으로 돌아간다.
            self._daily_high_cache_date = today
        refreshed = self._daily_bar_repository.refreshed_today(codes, today) if self._daily_bar_repository is not None else set()
        # 기존 ka10001 250일 최고가는 권리 조정 전 값이었다. 설치 후 한 번은
        # 모든 현재 종목을 ka10081 수정주가 기준으로 다시 계산해 교체한다.
        refresh_adjusted_basis = self._settings.get("daily_high_adjusted_basis_version") != "1"
        missing = daily_high_candidates(
            codes, self._daily_highs, refreshed,
            force=force, adjusted_basis_refresh=refresh_adjusted_basis,
        )
        if not missing:
            return False
        if refresh_adjusted_basis:
            self._daily_high_basis_refresh_expected = set(missing)
            self._daily_high_basis_refresh_received.clear()
        return self._daily_high_worker_controller.start(
            missing, followup_codes=codes
        )

    def _on_daily_high_received(self, code: str, targets: object) -> None:
        if isinstance(targets, DailyHighTargets):
            target_day = self._after_close_finalization_date
            if code in self._after_close_finalization_codes and target_day is not None and any(
                bar.trade_date == target_day.strftime("%Y%m%d") for bar in targets.daily_bars
            ):
                self._after_close_daily_received.add(code)
            self._daily_highs[code] = targets
            self._daily_high_basis_refresh_received.add(code)
            if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_adjusted_high_250_price"):
                self._stock_lookup.update_adjusted_high_250_price(code, targets.high_250_price)
            if self._daily_bar_repository is not None:
                try:
                    now = self._ranking_now()
                    self._daily_bar_repository.upsert_targets(
                        code, targets, now.date(), observed_at=now
                    )
                except Exception as error:
                    logger.warning("일봉 캐시 저장 실패 (%s): %s", code, error)
            if targets.previous_day_trade_value_eok is not None:
                self._previous_day_trade_values[code] = targets.previous_day_trade_value_eok
            if targets.previous_day_close_price is not None:
                self._previous_day_close_prices[code] = targets.previous_day_close_price
            if targets.daily_trade_values_eok:
                self._pending_daily_trade_comparisons[code] = targets.daily_trade_values_eok
                if not self._daily_trade_comparison_timer.isActive():
                    self._daily_trade_comparison_timer.start()
            if self._defer_table_update_while_modal():
                return
            self._render_new_high_price(code)
            self._render_high_distance(code)
            self._render_trade_values(code)

    def _finalization_minute_bars_complete(self, code: str, bars: tuple[object, ...]) -> bool:
        target_day = self._after_close_finalization_date
        if target_day is None:
            return False
        cached_nxt = (
            self._stock_lookup.load_cached_nxt_enabled((code,))
            if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_cached_nxt_enabled")
            else {}
        )
        return minute_bars_complete(
            (bar.minute for bar in bars if isinstance(bar, MinuteOhlcv)),
            target_day,
            cached_nxt.get(code, True),
        )

    def _on_daily_high_worker_finished(self, codes: tuple[str, ...]) -> None:
        """수정주가 기준 250일 최고가 재계산 완료 여부를 기록한다."""
        if self._after_close_finalization_codes:
            attempted = set(self._after_close_finalization_codes)
            target_day = self._after_close_finalization_date
            outcome = evaluate_finalization_outcome(
                attempted, self._after_close_minute_received, self._after_close_daily_received,
                target_day, self._finalization_attempts, self._ranking_now(),
            ) if target_day is not None else None
            completed = outcome.completed if outcome is not None else ()
            if completed and self._daily_bar_repository is not None and self._after_close_finalization_date is not None:
                self._daily_bar_repository.mark_finalized(completed, self._after_close_finalization_date)
            if target_day is not None and self._entry_snapshot_writer is not None:
                # 분봉·일봉 확정 흐름이 끝난 뒤, 당일 체결 중 0w 누락분만 종목당 1회 조회한다.
                self._entry_snapshot_writer.enqueue_program_backfill(target_day, tuple(sorted(attempted)))
            if target_day is not None:
                assert outcome is not None
                for code in outcome.retry_codes:
                    key = (target_day, code)
                    self._finalization_retry_after[key] = outcome.retry_at
                if self._daily_bar_repository is not None:
                    for code, missing in outcome.unconfirmed:
                        key = (target_day, code)
                        self._daily_bar_repository.mark_unconfirmed(
                            code, target_day, missing, self._finalization_attempts.get(key, 0),
                        )
                        logger.warning(
                            "장 마감 자료 미확정: %s · %s · 누락=%s · 시도=%d",
                            target_day, code, ",".join(missing), self._finalization_attempts.get(key, 0),
                        )
            self._after_close_finalization_codes.clear()
            self._after_close_finalization_date = None
            self._after_close_minute_received.clear()
            self._after_close_daily_received.clear()
        if self._daily_high_basis_refresh_expected:
            if self._daily_high_basis_refresh_expected <= self._daily_high_basis_refresh_received:
                self._settings.set("daily_high_adjusted_basis_version", "1")
            self._daily_high_basis_refresh_expected.clear()
            self._daily_high_basis_refresh_received.clear()
        self._secondary_data_coordinator.daily_high_finished(codes)

    def _load_cached_historical_highs(self, codes: tuple[str, ...]) -> None:
        if self._stock_lookup is None or not hasattr(self._stock_lookup, "load_historical_high_prices"):
            return
        try:
            self._historical_high_prices.update(self._stock_lookup.load_historical_high_prices(codes))
        except Exception as error:
            logger.warning("역사적 신고가 캐시 조회 실패: %s", error)

    def _start_historical_high_loading(self, codes: tuple[str, ...]) -> bool:
        if self._closing or self._ranking_execution.priority_preparing or not self._historical_high_worker_controller.available:
            return False
        if self._historical_high_worker_controller.is_running:
            return True
        refresh_basis = self._settings.get("historical_high_adjusted_basis_version") != "5"
        today = self._ranking_now().strftime("%Y-%m-%d")
        checked_today = (
            self._stock_lookup.historical_high_checked_today(codes, today)
            if self._stock_lookup is not None and hasattr(self._stock_lookup, "historical_high_checked_today")
            else set()
        )
        missing = codes if refresh_basis else tuple(code for code in codes if code not in checked_today)
        if not missing:
            return False
        if refresh_basis:
            self._historical_high_refresh_expected = set(missing)
            self._historical_high_refresh_received.clear()
        return self._historical_high_worker_controller.start(missing)

    def _on_historical_high_received(self, code: str, target: object) -> None:
        if not isinstance(target, HistoricalHighTarget) or target.price is None:
            return
        self._historical_high_prices[code] = target.price
        self._historical_high_refresh_received.add(code)
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_historical_high_price"):
            self._stock_lookup.update_historical_high_price(
                code, target.price, target.first_year, target.last_year, self._ranking_now().strftime("%Y-%m-%d"),
                occurred_on=target.occurred_on, evidence=target.evidence,
            )
        if self._defer_table_update_while_modal():
            return
        self._render_new_high_price(code)
        self._render_high_distance(code)

    def _on_historical_high_worker_finished(self) -> None:
        if self._historical_high_refresh_expected:
            if self._historical_high_refresh_expected <= self._historical_high_refresh_received:
                self._settings.set("historical_high_adjusted_basis_version", "5")
            self._historical_high_refresh_expected.clear()
            self._historical_high_refresh_received.clear()

    def _load_cached_daily_highs(self, codes: tuple[str, ...]) -> None:
        """장외에도 최근 저장 일봉으로 직전 1일·신고가를 즉시 표시한다."""
        if self._daily_bar_repository is None:
            return
        try:
            cached = self._daily_bar_repository.load_targets(tuple(code for code in codes if code not in self._daily_highs))
        except Exception as error:
            logger.warning("일봉 캐시 조회 실패: %s", error)
            return
        for code, targets in cached.items():
            self._daily_highs[code] = targets
            if targets.previous_day_trade_value_eok is not None:
                self._previous_day_trade_values[code] = targets.previous_day_trade_value_eok
            if targets.previous_day_close_price is not None:
                self._previous_day_close_prices[code] = targets.previous_day_close_price
            if self._row_by_code.get(code) is not None and not self._defer_table_update_while_modal():
                self._render_new_high_price(code)
                self._render_high_distance(code)
                self._render_trade_values(code)

    def _render_trade_values(self, code: str, *, live_only: bool = False) -> None:
        row = self._row_by_code.get(code)
        if row is None:
            return
        now = self._ranking_now()
        live_values = (
            self._minute_aggregator.bucket_trade_value_eok(code, 1, now),
            self._minute_aggregator.bucket_trade_value_eok(code, 5, now),
            self._minute_aggregator.bucket_trade_value_eok(code, 60, now),
            self._minute_aggregator.today_trade_value_eok(code, now),
        )
        completed_values = (
            self._minute_aggregator.bucket_trade_value_eok(code, 1, now, previous=True),
            self._minute_aggregator.bucket_trade_value_eok(code, 5, now, previous=True),
            self._minute_aggregator.bucket_trade_value_eok(code, 60, now, previous=True),
            self._previous_day_trade_values.get(code, 0.0),
        )
        periods = ("1m", "5m", "60m", "day")
        values = tuple(
            completed_values[index] if self._trade_display_mode(period) == "completed" else live_values[index]
            for index, period in enumerate(periods)
        )
        visible_values = zip(range(6, 7), ("1m",), values[:1]) if live_only else zip(range(6, 10), ("1m", "5m", "60m", "day"), values)
        for column, period, value in visible_values:
            item = QTableWidgetItem(f"{value:.{self._decimal_places('trade_value')}f}")
            threshold = float(self._settings.get(f"trade_value_{period}_alert_eok"))
            is_alert = self._settings.get("trade_value_alert_enabled") == "1" and threshold > 0 and value >= threshold
            row = self._row_by_code.get(code, 0)
            item.setData(self.TRADE_VALUE_ALERT_ROLE, is_alert)
            item.setBackground(self.TRADE_VALUE_ALERT_COLOR if is_alert else self._row_background_color(code, row))
            item.setForeground(QColor("#C00000") if is_alert else QColor("black"))
            font = item.font()
            font.setBold(is_alert)
            item.setFont(font)
            self._table.setItem(row, column, item)
        fundamentals = self._fundamentals.get(code)
        if fundamentals:
            strength_source = values
            strength_values = zip((4,), ("1m",), strength_source[:1]) if live_only else zip((4, 10, 11, 12), ("1m", "5m", "60m", "day"), strength_source)
            for column, period, value in strength_values:
                previous_day = period == "day" and self._trade_display_mode("day") == "completed"
                # 유통주식 수와 현재가가 있으면 직접 계산하고, 그렇지 않으면
                # 장중 0B 시가총액(311)·유통비율, 마지막으로 ka10001 캐시를 쓴다.
                strength = trade_strength_percent(
                    value,
                    fundamentals,
                    current_price=self._previous_day_close_prices.get(code) if previous_day else self._current_prices.get(code),
                    market_cap_eok=None if previous_day else self._market_cap_eok(code, fundamentals),
                )
                interest = float(self._settings.get(f"strength_{period}_interest"))
                caution = float(self._settings.get(f"strength_{period}_caution"))
                fire = float(self._settings.get(f"strength_{period}_fire"))
                item = QTableWidgetItem(strength_badge(
                    strength,
                    interest,
                    caution,
                    fire,
                    self._settings.get("strength_show_icon") == "1",
                    self._decimal_places("strength"),
                    self._strength_badge_icons(),
                ))
                level = "fire" if strength is not None and strength >= fire else "caution" if strength is not None and strength >= caution else "interest" if strength is not None and strength >= interest else ""
                image = self._strength_icon_image_path(level) if level else None
                if image is not None:
                    item.setIcon(QIcon(str(image)))
                item.setBackground(self._row_background_color(code, row))
                is_fire = strength is not None and strength >= fire
                item.setForeground(QColor("#C00000") if is_fire else QColor("#C65911") if strength is not None and strength >= caution else QColor("#806000") if strength is not None and strength >= interest else QColor("black"))
                font = item.font()
                font.setBold(is_fire)
                item.setFont(font)
                self._table.setItem(row, column, item)
        self._schedule_theme_trade_summary()

    def _render_current_price(self, code: str) -> None:
        """현재가를 갱신해도 신고가·순위 행 강조 배경을 유지한다."""
        row = self._row_by_code.get(code)
        current_price = self._current_prices.get(code)
        if row is None or current_price is None:
            return
        item = QTableWidgetItem(f"{current_price:,}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setBackground(self._row_background_color(code, row))
        self._table.setItem(row, 5, item)

    def _schedule_theme_trade_summary(self) -> None:
        if hasattr(self, "_theme_trade_summary_timer") and not self._theme_trade_summary_timer.isActive():
            self._theme_trade_summary_timer.start()

    def _cycle_theme_trade_summary_period(self) -> None:
        periods = ("1m", "5m", "60m", "day")
        current = self._settings.get("theme_trade_summary_period")
        next_period = periods[(periods.index(current) + 1) % len(periods)] if current in periods else periods[0]
        self._settings.set("theme_trade_summary_period", next_period)
        self._refresh_theme_trade_summary()
        if bool(getattr(self, "_theme_group_sort_enabled", False)):
            self._sort_visible_rows_by_theme_group(True)
        period_labels = {"1m": "1분", "5m": "5분", "60m": "60분", "day": "1일"}
        self.statusBar().showMessage(f"상위 테마 거래대금 기준: {period_labels[next_period]}")

    def _refresh_theme_trade_summary(self) -> None:
        # 창 종료 직전에 이미 큐에 들어간 single-shot 타이머가 삭제된 테스트
        # DB나 종료 중인 실제 DB를 다시 열지 않도록 한다.
        if self._closing:
            return
        show_summary = self._settings.get("theme_trade_summary_enabled") == "1"
        show_excluded_summary = self._settings.get("theme_trade_summary_excluded_enabled") == "1"
        if not show_summary and not show_excluded_summary:
            self._theme_trade_summary.setVisible(False)
            self._theme_trade_excluded_summary.setVisible(False)
            return
        self._theme_trade_summary.setVisible(show_summary)
        self._theme_trade_excluded_summary.setVisible(show_excluded_summary)
        if not self._ranked_stock_names:
            if show_summary:
                self._theme_trade_summary.setText("상위 테마 거래대금: 순위 조회 후 표시됩니다.")
            if show_excluded_summary:
                self._theme_trade_excluded_summary.setText("제외 종목 반영 상위 테마 거래대금: 순위 조회 후 표시됩니다.")
            return
        now = self._ranking_now()
        period = self._settings.get("theme_trade_summary_period")
        period_labels = {"1m": "1분", "5m": "5분", "60m": "60분", "day": "1일"}
        period = period if period in period_labels else "day"
        minutes = {"1m": 1, "5m": 5, "60m": 60}
        digits = self._decimal_places("trade_value")
        if show_summary:
            entries: list[tuple[str, str, float]] = []
            for code, name in self._ranked_stock_names.items():
                value = self._minute_aggregator.today_trade_value_eok(code, now) if period == "day" else self._minute_aggregator.bucket_trade_value_eok(code, minutes[period], now)
                entries.append((code, name, value))
            top = top_theme_trade_values(entries, self._themes)
            if not top:
                self._theme_trade_summary.setText("상위 테마 거래대금: 테마가 지정된 종목이 없습니다.")
            else:
                values = self._theme_trade_summary_values(top, digits)
                self._theme_trade_summary.setText(f"상위 테마 거래대금 (실시간 조회 상위 20종목 · {period_labels[period]}): {values}")
        excluded_names = parse_themes(self._settings.get("theme_trade_summary_excluded_stocks"), ",/|;")
        excluded = {"".join(value.split()).casefold() for value in excluded_names}
        if not show_excluded_summary:
            return
        if not excluded:
            self._theme_trade_excluded_summary.setText("제외 종목 반영 상위 테마 거래대금: 제외 종목 목록을 입력하세요.")
            return
        excluded_entries: list[tuple[str, str, float]] = []
        for code, name in self._ranked_stock_names.items():
            if code.casefold() in excluded or "".join(name.split()).casefold() in excluded:
                continue
            value = self._minute_aggregator.today_trade_value_eok(code, now) if period == "day" else self._minute_aggregator.bucket_trade_value_eok(code, minutes[period], now)
            excluded_entries.append((code, name, value))
        excluded_top = top_theme_trade_values(
            excluded_entries, self._themes, excluded_values=excluded_names,
        )
        self._theme_trade_excluded_summary.setVisible(True)
        self._theme_trade_excluded_summary.setText(
            f"제외 종목 반영 상위 테마 거래대금 (실시간 조회 상위 20종목 · {period_labels[period]}): "
            f"{self._theme_trade_summary_values(excluded_top, digits) if excluded_top else '표시할 테마가 없습니다.'}"
        )

    def _theme_trade_summary_values(self, top: list[tuple[str, float]], digits: int) -> str:
        return theme_trade_summary_html(top, digits)

    @staticmethod
    def _format_trade_value_eok(value: float, digits: int) -> str:
        return format_trade_value_eok(value, digits)

    @staticmethod
    def _trade_value_color(value: float) -> str:
        return trade_value_color(value)
    def _apply_near_high_background(self, code: str) -> None:
        self._apply_row_background(code)

    def _apply_row_background(self, code: str) -> None:
        row = self._row_by_code.get(code)
        if row is None:
            return
        color = self._row_background_color(code, row)
        for column in range(self._table.columnCount()):
            item = self._table.item(row, column)
            if item is not None:
                item.setBackground(
                    self.TRADE_VALUE_ALERT_COLOR
                    if bool(item.data(self.TRADE_VALUE_ALERT_ROLE))
                    else color
                )
            widget = self._table.cellWidget(row, column)
            if widget is not None:
                palette = widget.palette()
                palette.setColor(QPalette.ColorRole.Window, color)
                widget.setPalette(palette)
                widget.setAutoFillBackground(True)
                widget.setStyleSheet(f"background-color: {color.name()};")

    def _row_background_color(self, code: str, row: int) -> QColor:
        rank_item = self._table.item(row, 0) if hasattr(self, "_table") else None
        try:
            rank = int(rank_item.text()) if rank_item is not None else row + 1
        except ValueError:
            rank = row + 1
        return QColor(row_background_color(
            near_high=code in self._near_high_codes,
            selected=code == self._selected_table_code,
            rank_changed=code in self._rank_changed_codes,
            rank_changed_enabled=self._settings.get("rank_changed_highlight_enabled") == "1",
            rank_changed_color=self._settings.get("rank_changed_row_color"),
            rank=rank,
            odd_color=self._settings.get("rank_row_odd_color"),
            even_color=self._settings.get("rank_row_even_color"),
        ))

    def _start_rank_changed_highlights(self) -> None:
        self._rank_changed_highlight_timer.stop()
        if not self._rank_changed_codes:
            return
        if self._settings.get("rank_changed_highlight_enabled") != "1":
            self._clear_rank_changed_highlights()
            return
        duration_ms = rank_highlight_duration_ms(
            self._settings.get("rank_changed_highlight_seconds")
        )
        if duration_ms <= 0:
            self._clear_rank_changed_highlights()
            return
        logger.info("순위 변동 강조 시작: %s개 · %sms", len(self._rank_changed_codes), duration_ms)
        self._rank_changed_highlight_timer.start(duration_ms)

    def _clear_rank_changed_highlights(self) -> None:
        if not self._rank_changed_codes:
            return
        logger.info("순위 변동 강조 종료")
        self._rank_changed_codes.clear()
        for code in tuple(self._row_by_code):
            self._apply_near_high_background(code)
            self._render_trade_values(code)

    def _decimal_places(self, column: str) -> int:
        return decimal_places(self._settings.get(f"decimal_{column}"), column)

    @staticmethod
    def _change_rate_item(value: object) -> QTableWidgetItem:
        item = QTableWidgetItem(str(value))
        color = change_rate_text_color(value)
        if color is not None:
            item.setForeground(QColor(color))
        return item

    def _strength_badge_icons(self) -> tuple[str, str, str]:
        return tuple(
            "" if self._strength_icon_image_path(level) is not None else self._settings.get(f"strength_icon_{level}")
            for level in ("interest", "caution", "fire")
        )

    def _strength_icon_image_path(self, level: str) -> Path | None:
        if not level:
            return None
        stored = self._settings.get(f"strength_icon_{level}_image").strip()
        if not stored:
            return None
        path = self._api_config_path().parent.parent / stored
        return path if path.is_file() else None

    def _start_fundamentals_loading(self, codes: tuple[str, ...], *, force: bool = False) -> bool:
        if self._closing or self._ranking_execution.priority_preparing or not self._fundamentals_worker_controller.available:
            return False
        if self._fundamentals_worker_controller.is_running:
            return True
        refresh_codes: tuple[str, ...] | None = None
        if not force and self._stock_lookup is not None and hasattr(self._stock_lookup, "fundamentals_to_refresh"):
            refresh_codes = tuple(self._stock_lookup.fundamentals_to_refresh(codes, self._ranking_now().strftime("%Y-%m-%d")))
        missing_codes = fundamentals_candidates(
            codes, self._fundamentals, refresh_codes, force=force,
        )
        if not missing_codes:
            return False
        return self._fundamentals_worker_controller.start(
            missing_codes, followup_codes=codes
        )

    def _on_fundamentals_received(self, code: str, fundamentals: object) -> None:
        if isinstance(fundamentals, StockFundamentals):
            # ka10001의 250일 최고가는 KRX 단독·권리 조정 전 값일 수 있다.
            # DB 저장만 보호하고 화면 메모리를 원본 응답으로 교체하면 앱 시작
            # 5초 뒤 보완 조회 시 KRX+NXT 신고가가 KRX 값으로 내려간다.
            # 현재 실행에서 계산한 수정주가 일봉값을 우선하고, 아직 일봉값이
            # 없으면 시작 때 읽은 마지막 정상 캐시를 유지한다.
            fundamentals = merge_fundamentals_with_adjusted_high(
                fundamentals,
                existing=self._fundamentals.get(code),
                daily=self._daily_highs.get(code),
            )
            self._fundamentals[code] = fundamentals
            if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_fundamentals"):
                # ka10001 원본 250일 고가는 권리 조정 전 가격일 수 있으므로
                # daily high 작업이 저장한 수정주가 기준 캐시를 덮어쓰지 않는다.
                self._stock_lookup.update_fundamentals(code, fundamentals.market_cap_eok, fundamentals.float_ratio_percent, None, fundamentals.float_shares)
            if self._defer_table_update_while_modal():
                return
            self._render_market_cap(code)
            self._render_new_high_price(code)
            self._render_high_distance(code)
            self._render_trade_values(code)

    def _render_market_cap(self, code: str) -> None:
        row = self._row_by_code.get(code)
        fundamentals = self._fundamentals.get(code)
        if row is None or fundamentals is None:
            return
        market_cap_eok = self._market_cap_eok(code, fundamentals)
        level = self._market_cap_highlight_level(market_cap_eok)
        item = QTableWidgetItem(self._format_market_cap_eok(market_cap_eok))
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setForeground(QColor(self._market_cap_highlight_color(market_cap_eok)))
        item.setBackground(self._row_background_color(code, row))
        self._table.setItem(row, 15, item)
        self._table.removeCellWidget(row, 15)
        if level is not None and self._settings.get("market_cap_highlight_badge_enabled") == "1":
            badge = QLabel(item.text())
            badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            badge_color = self._settings.get(f"market_cap_highlight_{level}_badge_color")
            text = self._settings.get(f"market_cap_highlight_{level}_color")
            badge.setStyleSheet(f"background:{badge_color}; color:{text}; border-radius:5px; padding:2px 6px;")
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(3, 2, 3, 2)
            layout.addStretch()
            layout.addWidget(badge)
            self._table.setCellWidget(row, 15, container)

    def _market_cap_eok(self, code: str, fundamentals: StockFundamentals) -> float:
        """장중 0B 시가총액(311)을 우선하고, 없으면 ka10001 캐시를 쓴다."""
        return self._realtime_market_caps.get(code, fundamentals.market_cap_eok)

    def _market_cap_highlight_level(self, market_cap_eok: float) -> str | None:
        if self._settings.get("market_cap_highlight_enabled") != "1":
            return None
        for level in ("high", "middle", "low"):
            try:
                threshold = max(0.0, float(self._settings.get(f"market_cap_highlight_{level}_eok")))
            except (TypeError, ValueError):
                threshold = 0.0
            if threshold > 0 and market_cap_eok >= threshold:
                return level
        return None

    def _market_cap_highlight_color(self, market_cap_eok: float) -> str:
        """시가총액 구간별 전체 글자색을 반환한다."""
        level = self._market_cap_highlight_level(market_cap_eok)
        return self._settings.get(f"market_cap_highlight_{level}_color") if level else "#333333"

    def _format_market_cap_eok(self, value: float) -> str:
        return format_market_cap_eok(value)

    def _on_fundamentals_completed(self, codes: object = ()) -> None:
        codes = tuple(codes) if isinstance(codes, (tuple, list)) else ()
        if not self._closing and codes:
            self._secondary_data_coordinator.fundamentals_finished(
                codes, after_hours_pause=self._is_after_hours_data_pause(),
            )

    def _on_realtime_failure(self, message: str) -> None:
        logger.warning("실시간 체결 연결 실패: %s", message)
        if self._active_api_route in {"central", "central_waiting"}:
            self._active_api_route = "central_retry"
            self._set_connected_api_status()
        self.statusBar().showMessage("실시간 체결 연결에 실패했습니다. 새로고침으로 다시 시도하세요.")

    def changeEvent(self, event: QEvent) -> None:
        """독립 뉴스 프로세스의 최소화 상태만 메인창과 맞춘다."""
        super().changeEvent(event)
        if not self._news_process_manager.is_running:
            return
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._news_restore_sync_timer.stop()
                self._news_restore_sync_pending = False
                self._send_news_command(action="minimize")
            else:
                self._news_restore_sync_pending = True
                self._send_news_command(action="restore")
                self._news_restore_sync_timer.start()
        elif event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            if not self._news_restore_sync_pending:
                self._sync_news_window()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closing:
            self._closing = True
            self._top20_trade_value_window._geometry_save_timer.stop()
            self._top20_trade_value_window._save_window_geometry()
            partial = self._top20_collector.partial_record(
                self._top20_trade_value, self._top20_market,
            )
            if partial is not None:
                self._save_top20_record(partial)
            self._window_geometry_save_timer.stop()
            self._save_window_geometry()
            self._save_columns()
            self._ranking_timer.stop()
            self._rank_changed_highlight_timer.stop()
            self._realtime_session_timer.stop()
            self._ranking_preparation_timer.stop()
            self._clock_timer.stop()
            self._theme_trade_summary_timer.stop()
            self._top20_index_timer.stop()
            self._price_cache_timer.stop()
            self._google_drive_debounce.stop()
            self._save_current_price_cache()
            self._minute_bar_save_timer.stop()
            self._flush_pending_minute_bars()
            self._daily_trade_comparison_timer.stop()
            self._flush_daily_trade_comparisons()
            if hasattr(self, "_trade_tick_flush_timer"):
                self._trade_tick_flush_timer.stop()
            for player, _ in self._near_high_sound_players.values():
                player.stop()
            self._refresh_button.setEnabled(False)
            self.statusBar().showMessage("종료 중: 실행 중인 작업을 일시 중지하고 있습니다…")
            if self._google_drive_sync is not None and self._google_drive_sync.connected and self._settings.get("google_drive_auto_upload_on_exit") == "1" and (self._google_drive_dirty or self._google_drive_debounce.isActive()):
                self._start_google_drive_sync("upload", close_after=True)
            self._request_worker_stop()
            if not self._running_workers():
                self._stop_current_news_process()
                if self._journal_command_path is not None:
                    self._stop_current_journal_process()
                event.accept()
                return
            QTimer.singleShot(100, self._finish_shutdown)
            event.ignore()
            return
        if self._running_workers():
            event.ignore()
            return
        self._stop_current_news_process()
        if self._journal_command_path is not None:
            self._stop_current_journal_process()
        event.accept()

    def _workers(self) -> tuple[QThread | None, ...]:
        return (
            self._entry_snapshot_writer,
            self._realtime_worker,
            self._minute_history_worker,
            self._fundamentals_worker,
            self._daily_high_worker,
            self._historical_high_worker,
            self._nxt_eligibility_worker,
            self._new_high_worker,
            self._ranking_worker,
            self._image_theme_ocr_worker,
            self._krx_stock_catalog_worker,
            self._top20_repair_worker,
            self._google_drive_worker,
            self._update_check_worker,
            self._update_download_worker,
        )

    def _running_workers(self) -> tuple[QThread, ...]:
        return tuple(worker for worker in self._workers() if worker is not None and worker.isRunning())

    def _request_worker_stop(self) -> None:
        for worker in self._running_workers():
            worker.requestInterruption()

    def _finish_shutdown(self) -> None:
        running = self._running_workers()
        if running:
            self._request_worker_stop()
            names = {
                self._realtime_worker: "실시간 체결",
                self._minute_history_worker: "분봉 보완",
                self._fundamentals_worker: "기본정보",
                self._daily_high_worker: "신고가",
                self._historical_high_worker: "역사적 신고가",
                self._nxt_eligibility_worker: "NXT 확인",
                self._new_high_worker: "신고가 목록",
                self._ranking_worker: "실시간 순위",
                self._google_drive_worker: "Google Drive",
                self._update_check_worker: "업데이트 확인",
                self._update_download_worker: "업데이트 다운로드",
            }
            labels = [names.get(worker, "백그라운드 작업") for worker in running]
            self.statusBar().showMessage(f"종료 중: {', '.join(labels)} 작업을 중단하는 중입니다…")
            QTimer.singleShot(250, self._finish_shutdown)
            return
        self.close()

    def _apply_table_visuals(self, *, refresh_theme_badges: bool = True) -> None:
        if not hasattr(self, "_table"):
            return
        palette = self._table.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(self._settings.get("rank_row_odd_color")))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(self._settings.get("rank_row_even_color")))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#DDEBF7"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000"))
        self._table.setPalette(palette)
        self._table.setAlternatingRowColors(True)
        font_size = int(self._settings.get("ui_font_size"))
        row_height = int(self._settings.get("ui_row_height"))
        responsive = self._settings.get("ui_mode") == "responsive"
        manual_size_hold = self._column_controller.manual_size_hold
        if row_height and (not responsive or manual_size_hold):
            self._table.verticalHeader().setDefaultSectionSize(row_height)
        elif responsive:
            # 자동 UI의 행 높이는 창의 세로 공간과 표시 행 수에 맞춘다.
            # 너무 작아져 읽기 어려운 경우와 지나치게 커지는 경우는 제한한다.
            available_height = self._table.viewport().height()
            fallback_height = self._table.font().pointSize() * 2 + 10
            self._table.verticalHeader().setDefaultSectionSize(
                responsive_row_height(
                    self._table.rowCount(), available_height, fallback_height,
                )
            )
        if font_size:
            font = self._table.font(); font.setPointSize(font_size); self._table.setFont(font)
        elif responsive:
            # 가로 폭에는 반응하지 않는다. 창을 좌우로 조절해도 글자 크기가
            # 흔들리지 않고, 행 높이를 바꾸거나 세로 크기를 바꿀 때만 따른다.
            effective_row_height = self._table.verticalHeader().defaultSectionSize()
            # 추정 비율 대신 실제 글자 높이를 재서, 행 안에서 잘리지 않는
            # 최대 글자 크기로 맞춘다. 행을 크게 하면 글자도 거의 꽉 차게 커진다.
            # 글자가 행을 거의 채우되, 잘림을 막을 3px 여백만 남긴다.
            available_height = max(8, effective_row_height - 3)
            font = self._table.font()
            for point_size in range(28, 5, -1):
                font.setPointSize(point_size)
                if QFontMetrics(font).height() <= available_height:
                    break
            else:
                font.setPointSize(6)
            self._table.setFont(font)
        icon_size = max(8, min(32, self._table.verticalHeader().defaultSectionSize() - 4))
        self._table.setIconSize(QSize(icon_size, icon_size))
        # 반응형 표 글자 크기가 변하면 이미 만들어진 테마 배지도 함께 갱신한다.
        if refresh_theme_badges:
            self._refresh_theme_badges()

    def _ensure_initial_ranking_rows_visible(self) -> None:
        """첫 정상 순위 수신 시 상위 20개 행이 한 화면에 들어오게 한다."""
        if self._initial_ranking_size_adjusted or self._table.rowCount() < 20:
            return
        required_height = self._table.rowCount() * self._table.verticalHeader().defaultSectionSize()
        shortfall = required_height - self._table.viewport().height()
        if shortfall > 0:
            current_width = self.width()
            self.resize(current_width, self.height() + shortfall)
            QTimer.singleShot(0, lambda: self.resize(current_width, self.height()))
        self._initial_ranking_size_adjusted = True

    def createPopupMenu(self) -> QMenu | None:
        """도구 모음 숨김 메뉴를 제공하지 않는다."""
        return None

    def eventFilter(self, watched: object, event: object) -> bool:
        if watched is self._table.viewport():
            position = getattr(event, "position", lambda: None)()
            point = position.toPoint() if position is not None else None
            event_type = getattr(event, "type", lambda: None)()
            if event_type == QEvent.Type.MouseButtonPress and point is not None and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton and self._rank_row_resize_target(point):
                self._row_height_dragging = True
                self._row_height_drag_start_y = point.y()
                self._row_height_drag_start_height = self._table.verticalHeader().defaultSectionSize()
                self._table.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
                return True
            if event_type == QEvent.Type.MouseMove and point is not None:
                if self._row_height_dragging:
                    self._apply_uniform_row_height(self._row_height_drag_start_height + point.y() - self._row_height_drag_start_y)
                    return True
                self._table.viewport().setCursor(
                    Qt.CursorShape.SizeVerCursor if self._rank_row_resize_target(point) else Qt.CursorShape.ArrowCursor
                )
            if event_type == QEvent.Type.MouseButtonRelease and self._row_height_dragging:
                self._row_height_dragging = False
                self._table.viewport().setCursor(Qt.CursorShape.ArrowCursor)
                return True
            if event_type == QEvent.Type.MouseButtonDblClick and point is not None:
                # 실제 셀이 아닌 오른쪽 또는 마지막 행 아래의 빈 공간에서만 동작한다.
                if self._table.itemAt(point) is None:
                    self._fit_window_to_table_contents()
                    return True
        return super().eventFilter(watched, event)  # type: ignore[arg-type]

    def _fit_window_to_table_contents(self) -> None:
        """표 내용만큼 창 크기를 맞춰 빈 공간과 불필요한 스크롤을 정리한다."""
        header_width = self._table.horizontalHeader().length()
        desired_table_width = header_width + self._table.verticalHeader().width() + self._table.frameWidth() * 2
        desired_table_height = (
            self._table.horizontalHeader().height()
            + sum(self._table.rowHeight(row) for row in range(self._table.rowCount()))
            + self._table.frameWidth() * 2
        )
        target_width, target_height = fitted_window_size(
            window_width=self.width(),
            window_height=self.height(),
            minimum_width=self.minimumWidth(),
            minimum_height=self.minimumHeight(),
            table_width=self._table.width(),
            table_height=self._table.height(),
            content_width=desired_table_width,
            content_height=desired_table_height,
        )
        self.resize(target_width, target_height)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_window_geometry_save_timer"):
            self._window_geometry_save_timer.start()
        if hasattr(self, "_news_dock_timer") and self._news_window_mode().startswith("docked_"):
            self._news_dock_timer.start()
        # 수동으로 열 폭을 조정한 뒤 30초 동안은 창을 어떻게 조절해도
        # 행 높이·열 너비를 모두 유지한다.
        if self._column_controller.manual_size_hold:
            return
        # 유예 시간이 지난 뒤에는 반응형 행 높이를 다시 계산한다.
        self._apply_table_visuals()
        if event.size().width() != event.oldSize().width():
            QTimer.singleShot(0, self._resize_columns_proportionally)

    def moveEvent(self, event: object) -> None:
        super().moveEvent(event)
        if hasattr(self, "_window_geometry_save_timer"):
            self._window_geometry_save_timer.start()
        if hasattr(self, "_news_dock_timer") and self._news_window_mode().startswith("docked_"):
            self._news_dock_timer.start()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # show 과정에서 예약된 자동 맞춤은 아직 막혀 있다. 모두 끝난 뒤부터
        # 실제 사용자의 창 크기 변경에만 자동 맞춤을 허용한다.
        if not self._column_controller.auto_fit_ready:
            QTimer.singleShot(0, self._enable_initial_column_auto_fit)

    def _save_window_geometry(self) -> None:
        """창 크기와 화면 위치를 현재 컴퓨터에 즉시 보관한다."""
        geometry = self.normalGeometry() if self.isMaximized() or self.isFullScreen() else self.geometry()
        # geometry()의 좌표는 제목 표시줄을 제외한 내용 영역 기준인데 move()는
        # 최상위 창 외곽 위치로 적용된다. 내용 좌표를 저장하면 재실행할 때마다
        # 제목 표시줄 높이만큼 아래·오른쪽으로 밀리므로 외곽 좌표를 보관한다.
        frame = self.frameGeometry()
        self._settings.set("window_width", str(geometry.width()))
        self._settings.set("window_height", str(geometry.height()))
        self._settings.set("window_x", str(frame.x()))
        self._settings.set("window_y", str(frame.y()))

    def _restore_window_position(self) -> None:
        position = parse_window_position(
            self._settings.get("window_x"), self._settings.get("window_y")
        )
        if position is None:
            return
        x, y = position
        # 모니터 구성 변경 후 창이 화면 밖에 남는 것을 방지한다.
        probe = QPoint(x + 40, y + 40)
        if any(screen.availableGeometry().contains(probe) for screen in QApplication.screens()):
            self.move(x, y)
