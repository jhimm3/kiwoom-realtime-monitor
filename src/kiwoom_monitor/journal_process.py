"""메인 실시간 표와 완전히 분리되어 실행되는 장중 매매일지."""

from __future__ import annotations

import argparse
import json
import os
import sys
import math
import html
import re
import sqlite3
import time as monotonic_time
import traceback
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QPoint, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QCloseEvent, QIcon, QImage, QPainter, QPen, QPolygon, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QColorDialog, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QScrollArea, QScrollBar, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget, QSpinBox,
    QMessageBox,
)

from kiwoom_monitor.application.minute_chart_service import MinuteChartService
from kiwoom_monitor.application.market_index_chart_service import MarketIndexChartService
from kiwoom_monitor.application.trade_history_service import TradeFill, TradeHistoryService
from kiwoom_monitor.application.trade_history_query_service import (
    TradeHistoryQueryService,
    backfill_affects_history_selection,
    history_backfill_cutoff,
    incomplete_trade_day_tasks,
    select_history_backfill_tasks,
    summarize_episode_bar_state,
    trade_episode_selection,
    trade_fill_selection,
)
from kiwoom_monitor.application.journal_chart_layout import (
    adaptive_price_grid_lines, average_price_label_positions, chart_close_information,
    chart_time_step_minutes, chart_time_tick_indices, daily_chart_rows as _daily_chart_rows,
    format_trade_value_eok, multi_day_time_tick_indices, rounded_price_grid,
    trade_callout_text, visible_trade_fills,
)
from kiwoom_monitor.application.trade_journal_summary import (
    TradeEpisode, TradeReview,
)
from kiwoom_monitor.application.trade_group_edit_service import TradeGroupEditService
from kiwoom_monitor.application.trade_episode_analysis_service import analyze_trade_cycles
from kiwoom_monitor.application.trade_analysis_preparation_service import TradeAnalysisPreparationService
from kiwoom_monitor.application.trade_journal_statistics import summarize_periods
from kiwoom_monitor.application.trade_chart import (
    DailyTradeChartService,
    aggregate_chart_rows,
    daily_chart_display_target,
    should_reuse_cached_daily_chart,
)
from kiwoom_monitor.application.trade_cost_service import DailyTradeCost, TradeCostService
from kiwoom_monitor.application.personal_trade_rules import (
    applicable_lesson_text, extract_structured_trade_rules, load_personal_trade_rules,
)
from kiwoom_monitor.application.strategy_pack import (
    STRATEGY_RESULT_MODES, default_strategy_pack,
)

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_journal_sync import (
    CentralJournalSyncRunner,
    CentralJournalSyncService,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.client_factory import create_query_client
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.infrastructure.persistence.journal_bar_repository import BarBackfillState
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_service import load_episode_entry_snapshots
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.news_process import _application_icon_path, _parent_is_alive, _set_taskbar_app_id
from kiwoom_monitor.presentation.journal_workers import (
    BackfillWorker, ConfirmWorker, DailyChartWorker, HistoryWorker, MarketIndexBackfillWorker,
)
from kiwoom_monitor.presentation.journal_delegates import JournalCellMarkerDelegate
from kiwoom_monitor.presentation.journal_settings_dialogs import (
    JournalChartSettingsDialog, JournalSettingsDialog, MOVING_AVERAGE_DEFAULTS,
)
from kiwoom_monitor.presentation.detached_chart_settings import (
    DetachedChartPanelSettingsDialog, DetachedChartSettingsDialog,
)
from kiwoom_monitor.presentation.journal_chart_widget import MinuteChart, trade_marker_polygon
from kiwoom_monitor.presentation.detached_chart_window import DetachedChartPanel, DetachedChartWindow
from kiwoom_monitor.presentation.process_control import (
    JsonCommandChannel,
    process_identity_document,
    read_process_identity,
)
from kiwoom_monitor.presentation.trade_review_view_model import (
    TradeHistoryEpisodeDisplay,
    build_trade_analysis_view_model,
    build_trade_history_summary_view_model,
)


class JournalWindow(QMainWindow):
    _AUTO_BACKFILL_BATCH_SIZE = 20

    def __init__(self, monitor_db: Path, journal_db: Path, config: Path) -> None:
        super().__init__()
        self._monitor_db, self._journal_db, self._config = monitor_db, journal_db, config
        self._journal_news_channel = JsonCommandChannel(
            monitor_db.parent / "journal_news_request.json", time_based_ids=True,
        )
        self._repo = JournalRepository(journal_db)
        self._trade_history_query = TradeHistoryQueryService(self._repo)
        self._trade_group_editor = TradeGroupEditService(self._repo)
        try:
            self._snapshot_news_repo: StockNewsRepository | None = StockNewsRepository(monitor_db.parent / "news.sqlite3")
        except Exception:
            self._snapshot_news_repo = None
        self._trade_analysis_preparer = TradeAnalysisPreparationService(
            self._repo,
            monitor_db,
            lambda code, started_at, ended_at: load_episode_entry_snapshots(
                self._repo, self._snapshot_news_repo, code, started_at, ended_at,
            ),
            lambda group_id, code: (
                self._snapshot_news_repo.load_journal_linked(group_id, code)
                if self._snapshot_news_repo is not None else ()
            ),
        )
        self._journal_settings = QSettings("KiwoomMonitor", "TradingJournalWindow")
        self._selection_row_enabled = True; self._selection_marker_enabled = True; self._selection_row_color = "#DDEBF7"
        self._personal_rules_path = self._journal_settings.value("personal_rules_path", "", type=str)
        self._personal_rules = self._repo.load_personal_rules()
        self._structured_personal_rules = self._repo.load_structured_personal_rules()
        self._strategy_packs = self._repo.load_strategy_packs()
        self._active_strategy_pack = default_strategy_pack()
        self._strategy_result_mode = self._journal_settings.value("strategy_result_mode", "together", type=str)
        if self._strategy_result_mode not in STRATEGY_RESULT_MODES:
            self._strategy_result_mode = "together"
        if self._personal_rules_path and Path(self._personal_rules_path).is_file():
            # 문서가 다시 선택되거나 내용이 바뀌었을 때 앞부분 캐시가 남지 않도록
            # 시작할 때 전체 문단을 다시 추출한다. 원문 파일은 DB에 넣지 않는다.
            extracted_rules = load_personal_trade_rules(Path(self._personal_rules_path))
            if extracted_rules and extracted_rules != self._personal_rules:
                self._personal_rules = extracted_rules
                self._repo.save_personal_rules(extracted_rules)
            structured_rules = extract_structured_trade_rules(Path(self._personal_rules_path))
            if structured_rules and structured_rules != self._structured_personal_rules:
                self._structured_personal_rules = structured_rules
                self._repo.save_structured_personal_rules(structured_rules)
        # 저작권이 있는 강의 구조는 저장소·배포본에 포함하지 않고 사용자 data/private에만 둔다.
        # 존재할 때만 기존 개인원칙 문서보다 정교한 강의/주제 경계를 사용한다.
        private_rulebook = monitor_db.parent / "private" / "미모사_강의_구조화_v2.md"
        private_structured_rules = extract_structured_trade_rules(private_rulebook)
        if private_structured_rules and private_structured_rules != self._structured_personal_rules:
            self._structured_personal_rules = private_structured_rules
            self._repo.save_structured_personal_rules(private_structured_rules)
        self._auto_history_sync_enabled = self._journal_settings.value("auto_history_sync_enabled", True, type=bool)
        self._chart_background = self._journal_settings.value("chart_background", "#F3F3F3", type=str)
        self._ctrl_wheel_zoom_enabled = self._journal_settings.value("ctrl_wheel_zoom_enabled", True, type=bool)
        try:
            self._estimated_buy_cost_rate = float(self._journal_settings.value("estimated_buy_cost_rate", "0.015", type=str))
            self._estimated_sell_cost_rate = float(self._journal_settings.value("estimated_sell_cost_rate", "0.215", type=str))
        except ValueError:
            self._estimated_buy_cost_rate, self._estimated_sell_cost_rate = 0.015, 0.215
        try:
            self._trade_value_threshold = float(self._journal_settings.value("trade_value_threshold_eok", "40", type=str))
        except ValueError:
            self._trade_value_threshold = 40.0
        try:
            self._daily_trade_value_threshold = float(self._journal_settings.value("daily_trade_value_threshold_eok", "0", type=str))
        except ValueError:
            self._daily_trade_value_threshold = 0.0
        self._show_dual_history_chart = self._journal_settings.value("show_dual_history_chart", False, type=bool)
        self._detached_chart_window: DetachedChartWindow | None = None
        self._shared_chart_annotations: dict[tuple[str, str], tuple[tuple[object, ...], ...]] = {}
        self._applying_shared_chart_state = False
        self._show_trade_details = self._journal_settings.value("show_trade_details", True, type=bool)
        try:
            self._drawing_line_width = float(self._journal_settings.value("drawing_line_width", "2", type=str))
        except ValueError:
            self._drawing_line_width = 2.0
        self._rectangle_color = self._journal_settings.value("rectangle_color", "#7B1FA2", type=str)
        self._line_color = self._journal_settings.value("line_color", "#7B1FA2", type=str)
        self._horizontal_line_color = self._journal_settings.value("horizontal_line_color", "#F57C00", type=str)
        self._vertical_line_color = self._journal_settings.value("vertical_line_color", "#00897B", type=str)
        self._moving_average_styles: dict[int, tuple[bool, str, float]] = {}
        for period, (default_enabled, default_color, default_width) in MOVING_AVERAGE_DEFAULTS.items():
            try:
                width = float(self._journal_settings.value(f"ma_{period}_width", str(default_width), type=str))
            except ValueError:
                width = default_width
            self._moving_average_styles[period] = (
                self._journal_settings.value(f"ma_{period}_enabled", default_enabled, type=bool),
                self._journal_settings.value(f"ma_{period}_color", default_color, type=str), width,
            )
        data_source = DataSourceConfig(config.with_name("data_source.json")).load()
        central_journal_sync = (
            CentralJournalSyncService(CentralContentClient(data_source.server_url, data_source.access_token))
            if data_source.mode in {"local_server", "personal_server"} else None
        )
        self._central_journal_sync_runner = CentralJournalSyncRunner(
            central_journal_sync,
            journal_db,
            lambda error: logging.getLogger(__name__).warning(
                "매매일지 설정 중앙 동기화 실패(로컬 자료 유지): %s", error,
            ),
        )
        if data_source.mode in {"local_server", "personal_server"}:
            self._api_ready = True
        else:
            settings = LocalApiConfig(config).load()
            self._api_ready = bool(settings.app_key and settings.secret_key)
        client = create_query_client(config, config.with_name("data_source.json"))
        self._minute_service = MinuteChartService(client, include_nxt=True)
        self._market_index_service = MarketIndexChartService(client)
        self._market_index_worker: MarketIndexBackfillWorker | None = None
        self._market_index_requested_days: set[date] = set()
        self._market_index_active_day: date | None = None
        self._pending_market_index_day: date | None = None
        self._compare_stock_requested: set[tuple[str, date]] = set()
        self._previous_chart_requested: set[tuple[str, date]] = set()
        self._daily_chart_service = DailyTradeChartService(client)
        self._history_service = TradeHistoryService(client)
        self._cost_service = TradeCostService(client)
        self._live_code = ""
        self._live_name = ""
        self._worker: ConfirmWorker | None = None
        self._history_worker: HistoryWorker | None = None
        self._quit_after_history_sync = False
        self._backfill_worker: BackfillWorker | None = None
        self._daily_chart_worker: DailyChartWorker | None = None
        self._pending_daily_chart: tuple[str, date, str] | None = None
        self._automatic_backfill_running = False
        self._automatic_backfill_attempted_for: tuple[date, bool] | None = None
        self._selected_history_day: date | None = None
        self._selected_history_code = ""
        self._selected_history_focus: datetime | None = None
        self._selected_history_range: tuple[date, date] | None = None
        self._visible_fills: tuple[TradeFill, ...] = ()
        self._visible_costs: tuple[DailyTradeCost, ...] = ()
        self._episodes: tuple[TradeEpisode, ...] = ()
        self._all_episodes: tuple[TradeEpisode, ...] = ()
        self._active_episode: TradeEpisode | None = None
        self._loading_review = False
        self._loading_setup = False
        self._last_confirmed_minute = ""
        self.setWindowTitle("키움 매매일지")
        self.resize(1040, 720)
        saved_geometry = QSettings("KiwoomMonitor", "TradingJournalWindow").value("geometry")
        if saved_geometry is not None:
            self.restoreGeometry(saved_geometry)
        root = QWidget(); layout = QVBoxLayout(root)
        self._status = QLabel("대기")
        self._tabs = QTabWidget(); layout.addWidget(self._tabs)

        history_page = QWidget(); history_layout = QVBoxLayout(history_page)
        filters = QHBoxLayout()
        self._from_date = QDateEdit(QDate.currentDate().addDays(-7)); self._from_date.setCalendarPopup(True)
        self._to_date = QDateEdit(QDate.currentDate()); self._to_date.setCalendarPopup(True)
        self._from_date.dateChanged.connect(self.reload_history); self._to_date.dateChanged.connect(self.reload_history)
        self._stock_filter = QLineEdit(); self._stock_filter.setPlaceholderText("종목명·코드 검색")
        self._stock_filter.setMaximumWidth(150); self._stock_filter.textChanged.connect(self._apply_history_filters)
        self._result_filter = QComboBox(); self._result_filter.addItems(("전체 손익", "수익", "손실", "보합"))
        self._result_filter.currentTextChanged.connect(self._apply_history_filters)
        self._review_filter = QComboBox(); self._review_filter.addItems(("전체 복기", "미작성", "작성 중", "복기 완료"))
        self._review_filter.currentTextChanged.connect(self._apply_history_filters)
        sync = QPushButton("키움에서 체결내역 가져오기"); sync.clicked.connect(self.sync_history)
        journal_settings = QPushButton("⚙"); journal_settings.setToolTip("매매일지 설정"); journal_settings.setFixedWidth(34)
        journal_settings.clicked.connect(self._open_journal_settings)
        filters.addWidget(QLabel("기간")); filters.addWidget(self._from_date); filters.addWidget(QLabel("~")); filters.addWidget(self._to_date)
        filters.addWidget(self._stock_filter); filters.addWidget(self._result_filter); filters.addWidget(self._review_filter)
        filters.addWidget(sync); filters.addStretch(); filters.addWidget(self._status); filters.addWidget(journal_settings)
        history_layout.addLayout(filters)
        self._period_summary = QLabel("체결내역을 가져오면 날짜·종목별 매매 결과가 표시됩니다.")
        self._period_summary.setStyleSheet("font-size: 14px; font-weight: 600; padding: 8px; background: #f4f7fb; border-radius: 4px;")
        export_review = QPushButton("복기 이미지 저장"); export_review.clicked.connect(self._export_review_image)
        summary_row = QHBoxLayout(); summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.addWidget(self._period_summary, 1); summary_row.addWidget(export_review)
        history_layout.addLayout(summary_row)
        group_actions = QHBoxLayout()
        split_group = QPushButton("선택 체결을 새 묶음으로 분리"); split_group.clicked.connect(self._split_selected_fills)
        merge_groups = QPushButton("선택한 매매 묶음 합치기"); merge_groups.clicked.connect(self._merge_selected_groups)
        reset_group = QPushButton("선택 묶음 자동분류로 복원"); reset_group.clicked.connect(self._reset_selected_groups)
        backfill = QPushButton("누락 분봉 일괄 보완"); backfill.clicked.connect(self._start_missing_backfill)
        retry_backfill = QPushButton("실패만 재시도"); retry_backfill.clicked.connect(lambda: self._start_missing_backfill(failed_only=True))
        stop_backfill = QPushButton("보완 중지"); stop_backfill.clicked.connect(self._stop_backfill)
        self._auto_backfill = QCheckBox("장 종료 후 자동 보완")
        self._auto_backfill.setChecked(QSettings("KiwoomMonitor", "TradingJournalWindow").value("auto_backfill", True, type=bool))
        self._auto_backfill.toggled.connect(self._save_auto_backfill_setting)
        group_actions.addWidget(split_group); group_actions.addWidget(merge_groups); group_actions.addWidget(reset_group); group_actions.addStretch()
        group_actions.addWidget(self._auto_backfill); group_actions.addWidget(backfill); group_actions.addWidget(retry_backfill); group_actions.addWidget(stop_backfill)
        history_layout.addLayout(group_actions)
        splitter = QSplitter(Qt.Orientation.Vertical); self._history_splitter = splitter
        self._summary_table = QTableWidget(0, 15)
        self._summary_table.setHorizontalHeaderLabels((
            "매매 기간", "종목", "매수", "매도", "매수금액", "매도금액",
            "추정 실현손익", "수익률", "실제 비용", "비용후 손익", "상태", "체결", "분봉", "복기", "매매유형",
        ))
        self._summary_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._summary_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._summary_table.itemSelectionChanged.connect(self._show_selected_summary)
        self._configure_journal_table(self._summary_table)
        splitter.addWidget(self._summary_table)
        self._fills_table = QTableWidget(0, 9)
        self._fills_table.setHorizontalHeaderLabels(("일시", "종목", "구분", "수량", "체결가", "체결금액", "주문유형", "시장", "주문번호"))
        self._fills_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._fills_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._fills_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._fills_table.itemSelectionChanged.connect(self._show_selected_fill)
        self._fills_table.cellClicked.connect(lambda row, _column: self._show_fill_at_row(row))
        self._configure_journal_table(self._fills_table)
        splitter.addWidget(self._fills_table)
        chart_box = QWidget(); chart_layout = QVBoxLayout(chart_box); chart_layout.setContentsMargins(0, 0, 0, 0); chart_layout.setSpacing(0)
        chart_actions = QHBoxLayout()
        self._history_interval = QComboBox(); self._history_interval.addItems(("1분", "3분", "5분", "10분", "30분", "60분", "일봉"))
        saved_history_interval = self._journal_settings.value("history_chart_interval", "1분", type=str)
        self._history_interval.setCurrentText(saved_history_interval if saved_history_interval in {"1분", "3분", "5분", "10분", "30분", "60분", "일봉"} else "1분")
        self._history_interval.currentTextChanged.connect(self._history_interval_changed)
        self._history_view_count = QSpinBox(); self._history_view_count.setRange(0, 2_000); self._history_view_count.setSingleStep(1)
        self._history_view_count.setSpecialValueText("전체"); self._history_view_count.setSuffix("개")
        self._history_view_count.setValue(self._journal_settings.value(f"history_chart_view_count_{self._history_interval.currentText()}", 120, type=int))
        self._history_view_count.valueChanged.connect(lambda value: self._history_chart.set_view_count(value))
        chart_larger = QPushButton("확대"); chart_larger.clicked.connect(lambda: self._history_chart.zoom(0.5))
        chart_smaller = QPushButton("축소"); chart_smaller.clicked.connect(lambda: self._history_chart.zoom(2.0))
        chart_all = QPushButton("전체"); chart_all.clicked.connect(lambda: self._history_view_count.setValue(0))
        history_drawing = QComboBox(); history_drawing.addItems(("그리기 끄기", "선", "가로선", "세로선", "사각형", "텍스트"))
        history_drawing.currentTextChanged.connect(lambda value: self._history_chart.set_drawing_mode(value.replace("그리기 ", "")))
        clear_history_drawing = QPushButton("그림 초기화"); clear_history_drawing.clicked.connect(lambda: self._history_chart.clear_annotations())
        chart_settings = QPushButton("⚙"); chart_settings.setToolTip("차트 설정"); chart_settings.setFixedWidth(34)
        chart_settings.clicked.connect(self._open_chart_settings)
        self._history_trade_details = QCheckBox("체결정보")
        self._history_trade_details.setToolTip("기본 차트와 차트 따로보기의 현재 종목 차트에서 체결시간·가격·수량을 표시합니다.")
        self._history_trade_details.setChecked(self._show_trade_details)
        self._history_trade_details.toggled.connect(self._set_trade_details_visible)
        self._dual_history_chart = QCheckBox("분봉+일봉 같이 보기")
        self._dual_history_chart.setChecked(self._show_dual_history_chart)
        self._dual_history_chart.toggled.connect(self._toggle_dual_history_chart)
        detached_charts = QPushButton("차트 따로 보기"); detached_charts.clicked.connect(self._open_detached_charts)
        chart_actions.addWidget(QLabel("봉 주기")); chart_actions.addWidget(self._history_interval); chart_actions.addWidget(QLabel("화면 봉 수")); chart_actions.addWidget(self._history_view_count)
        chart_actions.addWidget(chart_larger); chart_actions.addWidget(chart_smaller); chart_actions.addWidget(chart_all)
        chart_actions.addWidget(history_drawing); chart_actions.addWidget(clear_history_drawing); chart_actions.addWidget(chart_settings); chart_actions.addStretch()
        chart_actions.addWidget(self._dual_history_chart); chart_actions.addWidget(self._history_trade_details)
        chart_actions.addWidget(detached_charts)
        chart_layout.addLayout(chart_actions)
        self._history_chart = MinuteChart(); self._history_chart.set_background(self._chart_background); self._history_chart.set_ctrl_wheel_zoom_enabled(self._ctrl_wheel_zoom_enabled); self._history_chart.set_trade_value_threshold(self._trade_value_threshold); self._history_chart.set_daily_trade_value_threshold(self._daily_trade_value_threshold); self._history_chart.set_trade_details_visible(self._show_trade_details); self._history_chart.set_drawing_style(self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color); self._history_chart.set_moving_average_styles(self._moving_average_styles); self._history_chart.set_interval(self._history_interval.currentText()); self._history_chart.set_view_count(self._history_view_count.value()); chart_layout.addWidget(self._history_chart)
        self._history_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self._history_scroll.setFixedHeight(12)
        self._history_scroll.valueChanged.connect(self._history_chart.set_view_start)
        self._history_chart.view_changed.connect(lambda maximum, current, page: self._sync_chart_scrollbar(self._history_scroll, maximum, current, page))
        self._history_chart.view_count_changed.connect(self._history_view_count_changed)
        self._history_chart.annotations_changed.connect(lambda values: self._history_annotations_changed(self._history_interval.currentText(), values))
        chart_layout.addWidget(self._history_scroll)
        self._history_daily_title = QLabel("일봉 차트")
        self._history_daily_title.setFixedHeight(18)
        self._history_daily_title.setStyleSheet("font-weight:600;color:#555555;background:#EEF1F4;border-top:1px solid #BFC6CF;padding-left:5px;")
        self._history_daily_chart = MinuteChart(); self._history_daily_chart.set_interval("일봉")
        self._history_daily_chart.set_top_margin(30)
        self._history_daily_chart.set_background(self._chart_background)
        self._history_daily_chart.set_ctrl_wheel_zoom_enabled(self._ctrl_wheel_zoom_enabled)
        self._history_daily_chart.set_daily_trade_value_threshold(self._daily_trade_value_threshold)
        self._history_daily_chart.set_trade_details_visible(self._show_trade_details)
        self._history_daily_chart.set_drawing_style(self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color)
        self._history_daily_chart.set_moving_average_styles(self._moving_average_styles)
        self._history_daily_chart.set_view_count(self._journal_settings.value("history_chart_view_count_일봉", 120, type=int))
        self._history_daily_chart.view_count_changed.connect(lambda count: self._daily_history_view_count_changed(count))
        self._history_daily_chart.annotations_changed.connect(lambda values: self._history_annotations_changed("일봉", values))
        self._history_daily_title.setVisible(self._show_dual_history_chart)
        self._history_daily_chart.setVisible(self._show_dual_history_chart)
        chart_layout.addWidget(self._history_daily_title); chart_layout.addWidget(self._history_daily_chart)
        splitter.addWidget(chart_box)
        review_box = QGroupBox("선택한 매매 복기")
        review_form = QFormLayout(review_box)
        self._analysis_label = QTextEdit()
        self._analysis_label.setReadOnly(True)
        self._analysis_label.setPlainText("매매 묶음을 선택하면 분봉 기준 자동 분석이 표시됩니다.")
        self._analysis_label.setMinimumHeight(120)
        self._analysis_label.setMaximumHeight(220)
        self._analysis_label.setStyleSheet("padding: 7px; background: #f7f7f7; color: #333333;")
        self._analysis_data_status = QLabel("분석 자료: 매매 묶음을 선택하세요.")
        self._analysis_data_status.setWordWrap(True)
        self._analysis_data_status.setStyleSheet("padding: 4px 7px; color: #666666; background: #eef3f7;")
        setup_row = QVBoxLayout()
        self._setup_label = QTextEdit()
        self._setup_label.setReadOnly(True)
        self._setup_label.setPlainText("매매 묶음을 선택하면 진입 유형을 추정합니다.")
        self._setup_label.setMinimumHeight(58)
        self._setup_label.setMaximumHeight(100)
        self._setup_cycles = QTableWidget(0, 5)
        self._setup_cycles.setHorizontalHeaderLabels(("회차", "자동 유형", "세부 유형", "신뢰도", "직접 수정"))
        self._setup_cycles.verticalHeader().setVisible(False)
        self._setup_cycles.horizontalHeader().setStretchLastSection(True)
        self._setup_cycles.setMinimumHeight(68)
        self._setup_cycles.setMaximumHeight(260)
        setup_row.addWidget(self._setup_label)
        setup_row.addWidget(self._setup_cycles)
        self._review_reason = QTextEdit(); self._review_reason.setPlaceholderText("왜 진입했고 어떤 근거로 매도했는지 적어보세요."); self._review_reason.setMaximumHeight(70)
        self._review_note = QTextEdit(); self._review_note.setPlaceholderText("잘한 점, 놓친 점, 다음 매매에서 바꿀 점을 적어보세요."); self._review_note.setMaximumHeight(85)
        self._review_tags = QLineEdit(); self._review_tags.setPlaceholderText("예: 주도주 돌파, 테마주 돌파, 신규주 (쉼표로 구분)")
        review_options = QHBoxLayout()
        self._review_rating = QComboBox(); self._review_rating.addItems(("잘함", "보통", "아쉬움"))
        self._review_status = QComboBox(); self._review_status.addItems(("미작성", "작성 중", "복기 완료"))
        self._review_saved = QLabel("매매 묶음을 선택하세요.")
        save_review = QPushButton("지금 저장"); save_review.clicked.connect(self._save_active_review)
        self._journal_news_button = QPushButton("관련 뉴스 보기")
        self._journal_news_button.setEnabled(False)
        self._journal_news_button.clicked.connect(self._open_active_news)
        review_options.addWidget(QLabel("평가")); review_options.addWidget(self._review_rating)
        review_options.addWidget(QLabel("상태")); review_options.addWidget(self._review_status)
        review_options.addWidget(save_review); review_options.addWidget(self._journal_news_button); review_options.addWidget(self._review_saved); review_options.addStretch()
        review_form.addRow("예상 매매유형", setup_row)
        review_form.addRow("자동 분석", self._analysis_label)
        review_form.addRow("분석 자료", self._analysis_data_status)
        review_form.addRow("매매 이유", self._review_reason); review_form.addRow("복기 메모", self._review_note)
        review_form.addRow("태그", self._review_tags); review_form.addRow(review_options)
        review_scroll = QScrollArea()
        review_scroll.setWidgetResizable(True)
        review_scroll.setFrameShape(QFrame.Shape.NoFrame)
        review_scroll.setWidget(review_box)
        splitter.addWidget(review_scroll)
        splitter.setSizes((210, 170, 250, 210))
        saved_splitter = QSettings("KiwoomMonitor", "TradingJournalWindow").value("history_splitter")
        if saved_splitter is not None:
            splitter.restoreState(saved_splitter)
        history_layout.addWidget(splitter)
        help_text = QLabel("위 요약에서 날짜·종목을 선택하면 해당 체결만 아래에 표시되고, 분봉을 자동으로 보완해 매수·매도 위치를 차트에 표시합니다. 실현손익은 수수료·세금을 제외한 FIFO 추정값입니다.")
        help_text.setWordWrap(True); help_text.setStyleSheet("color: #666666;"); history_layout.addWidget(help_text)
        self._tabs.addTab(history_page, "과거 매매목록")

        # '오늘 진행 중' 탭은 화면에는 추가하지 않지만 set_stock(), 분봉 확인,
        # 차트 설정에서 내부 위젯을 계속 사용한다. 지역 변수로만 두면 __init__
        # 종료 후 QWidget과 자식 MinuteChart가 함께 삭제되므로 창 수명 동안
        # 명시적으로 보관한다.
        self._live_page = QWidget()
        live_page = self._live_page
        live_layout = QVBoxLayout(live_page)
        top = QHBoxLayout(); self._title = QLabel("메인 표에서 종목을 선택하면 오늘 분봉을 볼 수 있습니다.")
        self._live_interval = QComboBox(); self._live_interval.addItems(("1분", "3분", "5분", "10분", "30분", "60분", "일봉"))
        self._live_view_count = QSpinBox(); self._live_view_count.setRange(0, 2_000); self._live_view_count.setSingleStep(1)
        self._live_view_count.setSpecialValueText("전체"); self._live_view_count.setSuffix("개"); self._live_view_count.setValue(120)
        self._live_view_count.valueChanged.connect(lambda value: self._chart.set_view_count(value))
        live_zoom_in = QPushButton("확대"); live_zoom_in.clicked.connect(lambda: self._chart.zoom(0.5))
        live_zoom_out = QPushButton("축소"); live_zoom_out.clicked.connect(lambda: self._chart.zoom(2.0))
        live_all = QPushButton("전체"); live_all.clicked.connect(lambda: self._live_view_count.setValue(0))
        live_drawing = QComboBox(); live_drawing.addItems(("그리기 끄기", "선", "가로선", "세로선", "사각형", "텍스트"))
        live_drawing.currentTextChanged.connect(lambda value: self._chart.set_drawing_mode(value.replace("그리기 ", "")))
        clear_live_drawing = QPushButton("그림 초기화"); clear_live_drawing.clicked.connect(lambda: self._chart.clear_annotations())
        self._live_trade_details = QCheckBox("체결정보"); self._live_trade_details.setChecked(self._show_trade_details)
        self._live_trade_details.toggled.connect(self._set_trade_details_visible)
        confirm = QPushButton("완료 분봉 지금 확인"); confirm.clicked.connect(self.confirm_completed_bars)
        top.addWidget(self._title); top.addStretch(); top.addWidget(QLabel("봉 주기")); top.addWidget(self._live_interval)
        top.addWidget(QLabel("화면 봉 수")); top.addWidget(self._live_view_count)
        top.addWidget(live_zoom_in); top.addWidget(live_zoom_out); top.addWidget(live_all)
        top.addWidget(live_drawing); top.addWidget(clear_live_drawing); top.addWidget(self._live_trade_details); top.addWidget(confirm); live_layout.addLayout(top)
        self._chart = MinuteChart(); self._chart.set_background(self._chart_background); self._chart.set_ctrl_wheel_zoom_enabled(self._ctrl_wheel_zoom_enabled); self._chart.set_trade_value_threshold(self._trade_value_threshold); self._chart.set_daily_trade_value_threshold(self._daily_trade_value_threshold); self._chart.set_trade_details_visible(self._show_trade_details); self._chart.set_drawing_style(self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color); self._chart.set_moving_average_styles(self._moving_average_styles); live_layout.addWidget(self._chart)
        self._live_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self._live_scroll.valueChanged.connect(self._chart.set_view_start)
        self._chart.view_changed.connect(lambda maximum, current, page: self._sync_chart_scrollbar(self._live_scroll, maximum, current, page))
        self._chart.view_count_changed.connect(lambda count: self._sync_view_count_input(self._live_view_count, count))
        live_layout.addWidget(self._live_scroll)
        self._live_interval.currentTextChanged.connect(self._live_interval_changed)
        saved_interval = QSettings("KiwoomMonitor", "TradingJournalWindow").value("chart_interval", "1분", type=str)
        if saved_interval in ("1분", "3분", "5분", "10분", "30분", "60분", "일봉"):
            self._history_interval.setCurrentText(saved_interval); self._live_interval.setCurrentText(saved_interval)
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(("시간", "시가", "고가", "저가", "종가", "거래량", "거래대금(억)", "상태"))
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._configure_journal_table(self._table)
        live_layout.addWidget(self._table)
        note = QLabel("현재 진행 중인 봉은 ‘임시’, 키움에서 다시 받은 완료 봉은 ‘확인’으로 표시됩니다. 장 종료 후 다시 확인하면 같은 자리에 확정값이 덮어써집니다.")
        note.setWordWrap(True); live_layout.addWidget(note)
        # 실시간 순위표가 주 화면이므로 중복되는 '오늘 진행 중' 탭은 표시하지 않는다.

        backfill_page = QWidget(); backfill_layout = QVBoxLayout(backfill_page)
        backfill_actions = QHBoxLayout()
        self._backfill_filter = QComboBox(); self._backfill_filter.addItems(("전체", "미조회", "일부", "실패", "확정"))
        self._backfill_filter.currentTextChanged.connect(self._refresh_backfill_table)
        refresh_backfills = QPushButton("목록 새로고침"); refresh_backfills.clicked.connect(self._refresh_backfill_table)
        retry_selected = QPushButton("선택 항목 재조회"); retry_selected.clicked.connect(self._retry_selected_backfills)
        backfill_actions.addWidget(QLabel("상태")); backfill_actions.addWidget(self._backfill_filter)
        backfill_actions.addWidget(refresh_backfills); backfill_actions.addWidget(retry_selected); backfill_actions.addStretch()
        backfill_layout.addLayout(backfill_actions)
        self._backfill_table = QTableWidget(0, 5)
        self._backfill_table.setHorizontalHeaderLabels(("거래일", "종목코드", "상태", "분봉 수", "실패 사유"))
        self._backfill_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._backfill_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._backfill_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._backfill_table.horizontalHeader().setStretchLastSection(True)
        self._configure_journal_table(self._backfill_table)
        backfill_layout.addWidget(self._backfill_table)
        self._tabs.addTab(backfill_page, "분봉 보완 관리")

        stats_page = QWidget(); stats_layout = QVBoxLayout(stats_page)
        stats_actions = QHBoxLayout(); self._stats_unit = QComboBox(); self._stats_unit.addItems(("일간", "주간", "월간"))
        self._stats_unit.currentTextChanged.connect(self._refresh_statistics)
        self._stats_summary = QLabel(); self._stats_summary.setStyleSheet("font-size: 14px; font-weight: 600; padding: 8px; background: #f4f7fb;")
        stats_actions.addWidget(QLabel("집계")); stats_actions.addWidget(self._stats_unit); stats_actions.addStretch(); stats_layout.addLayout(stats_actions)
        stats_layout.addWidget(self._stats_summary)
        self._stats_table = QTableWidget(0, 7)
        self._stats_table.setHorizontalHeaderLabels(("기간", "매매", "수익", "손실", "승률", "실현손익", "수익률"))
        self._stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._configure_journal_table(self._stats_table)
        self._stats_table.horizontalHeader().setStretchLastSection(True); stats_layout.addWidget(self._stats_table)
        self._tabs.addTab(stats_page, "매매 통계")
        self.setCentralWidget(root)
        self._review_save_timer = QTimer(self); self._review_save_timer.setSingleShot(True)
        self._review_save_timer.timeout.connect(self._save_active_review)
        self._review_reason.textChanged.connect(self._review_changed); self._review_note.textChanged.connect(self._review_changed)
        self._review_tags.textChanged.connect(self._review_changed); self._review_rating.currentTextChanged.connect(self._review_changed)
        self._review_status.currentTextChanged.connect(self._review_changed)
        self._refresh_timer = QTimer(self); self._refresh_timer.timeout.connect(self.refresh); self._refresh_timer.start(1_000)
        self._confirm_timer = QTimer(self); self._confirm_timer.timeout.connect(self._confirm_on_new_minute); self._confirm_timer.start(1_000)
        self._auto_backfill_timer = QTimer(self); self._auto_backfill_timer.timeout.connect(self._check_automatic_backfill)
        self._auto_backfill_timer.start(60_000)
        self._history_sync_timer = QTimer(self); self._history_sync_timer.timeout.connect(self._auto_sync_history_once)
        self._history_sync_timer.start(600_000)
        self._central_sync_timer = QTimer(self); self._central_sync_timer.timeout.connect(self._schedule_central_journal_sync)
        self._central_sync_timer.start(60_000)
        self.reload_history()
        self._refresh_backfill_table()
        QTimer.singleShot(1_200, self._auto_sync_history_once)
        QTimer.singleShot(2_000, self._check_automatic_backfill)
        QTimer.singleShot(500, self._schedule_central_journal_sync)

    def _schedule_central_journal_sync(self) -> None:
        self._central_journal_sync_runner.schedule()

    def _journal_tables(self) -> tuple[QTableWidget, ...]:
        names = ("_summary_table", "_fills_table", "_table", "_backfill_table", "_stats_table")
        return tuple(value for name in names if isinstance((value := getattr(self, name, None)), QTableWidget))

    def _configure_journal_table(self, table: QTableWidget) -> None:
        table.setMouseTracking(True); table.viewport().setMouseTracking(True)
        delegate = JournalCellMarkerDelegate(table); delegate.marker_enabled = self._selection_marker_enabled
        table.setItemDelegate(delegate)
        table.cellClicked.connect(lambda row, column, current=table: self._select_journal_cell(current, row, column))
        table.itemSelectionChanged.connect(lambda current=table: self._apply_journal_row_highlight(current))

    def _select_journal_cell(self, table: QTableWidget, row: int, column: int) -> None:
        delegate = table.itemDelegate()
        if isinstance(delegate, JournalCellMarkerDelegate):
            delegate.set_selected_cell((row, column))
        self._apply_journal_row_highlight(table); table.viewport().update()

    def _apply_journal_row_highlight(self, table: QTableWidget) -> None:
        selected_rows = {index.row() for index in table.selectionModel().selectedRows()}
        if table.selectionBehavior() != QTableWidget.SelectionBehavior.SelectRows and table.currentRow() >= 0:
            selected_rows.add(table.currentRow())
        for row in range(table.rowCount()):
            brush: QBrush | QColor = QColor(self._selection_row_color) if self._selection_row_enabled and row in selected_rows else QBrush()
            for column in range(table.columnCount()):
                item = table.item(row, column)
                if item is not None:
                    item.setBackground(brush)
        table.viewport().update()

    def _set_trade_details_visible(self, visible: bool) -> None:
        self._show_trade_details = bool(visible)
        self._journal_settings.setValue("show_trade_details", self._show_trade_details)
        # The old live-chart page is intentionally not added to the tab widget.
        # Qt deletes that orphan page (and its children), so touching its cached
        # checkbox/chart references here raises "Internal C++ object already
        # deleted" before the visible history chart can be updated.
        for name in ("_history_trade_details",):
            checkbox = getattr(self, name, None)
            if isinstance(checkbox, QCheckBox):
                try:
                    if checkbox.isChecked() != self._show_trade_details:
                        checkbox.blockSignals(True); checkbox.setChecked(self._show_trade_details); checkbox.blockSignals(False)
                except RuntimeError:
                    # Keep a stale optional widget from blocking the visible chart.
                    setattr(self, name, None)
        for name in ("_history_chart", "_history_daily_chart"):
            chart = getattr(self, name, None)
            if isinstance(chart, MinuteChart):
                try:
                    chart.set_trade_details_visible(self._show_trade_details)
                except RuntimeError:
                    setattr(self, name, None)
        detached = getattr(self, "_detached_chart_window", None)
        if isinstance(detached, DetachedChartWindow):
            detached.set_trade_details_visible(self._show_trade_details)

    def _open_journal_settings(self) -> None:
        dialog = JournalSettingsDialog(
            self._personal_rules_path, self._estimated_buy_cost_rate, self._estimated_sell_cost_rate, self._auto_history_sync_enabled,
            self._chart_background, self._ctrl_wheel_zoom_enabled, self._trade_value_threshold,
            self._daily_trade_value_threshold, self._show_trade_details,
            self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color,
            self._strategy_packs, self._strategy_result_mode, self._repo, self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._strategy_packs = dialog.strategy_packs
        self._repo.save_strategy_packs(self._strategy_packs)
        self._strategy_result_mode = str(dialog.strategy_result_mode.currentData() or "together")
        previous_rules_path = self._personal_rules_path
        self._personal_rules_path = dialog.rules_path.text().strip()
        if not self._personal_rules_path:
            self._personal_rules = ()
            self._structured_personal_rules = ()
            self._repo.save_personal_rules(())
            self._repo.save_structured_personal_rules(())
        elif self._personal_rules_path != previous_rules_path or Path(self._personal_rules_path).is_file():
            extracted_rules = load_personal_trade_rules(Path(self._personal_rules_path))
            if extracted_rules:
                self._personal_rules = extracted_rules
                self._repo.save_personal_rules(extracted_rules)
            structured_rules = extract_structured_trade_rules(Path(self._personal_rules_path))
            if structured_rules:
                self._structured_personal_rules = structured_rules
                self._repo.save_structured_personal_rules(structured_rules)
        self._estimated_buy_cost_rate = dialog.estimated_buy_cost_rate.value()
        self._estimated_sell_cost_rate = dialog.estimated_sell_cost_rate.value()
        self._auto_history_sync_enabled = dialog.auto_history_sync.isChecked()
        self._journal_settings.setValue("personal_rules_path", self._personal_rules_path)
        self._journal_settings.setValue("estimated_buy_cost_rate", str(self._estimated_buy_cost_rate))
        self._journal_settings.setValue("estimated_sell_cost_rate", str(self._estimated_sell_cost_rate))
        self._journal_settings.setValue("auto_history_sync_enabled", self._auto_history_sync_enabled)
        self._journal_settings.setValue("strategy_result_mode", self._strategy_result_mode)
        if self._auto_history_sync_enabled:
            QTimer.singleShot(0, self._auto_sync_history_once)
        self._render_summaries()
        if self._active_episode is not None:
            rows = self._load_selected_chart_rows()
            self._show_trade_analysis(self._active_episode, rows)

    def _open_chart_settings(self) -> None:
        dialog = JournalChartSettingsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._chart_background = dialog.chart_background.text().strip() or "#F3F3F3"
        self._ctrl_wheel_zoom_enabled = dialog.ctrl_wheel_zoom.isChecked()
        self._trade_value_threshold = dialog.trade_value_threshold.value()
        self._daily_trade_value_threshold = dialog.daily_trade_value_threshold.value()
        self._show_trade_details = dialog.show_trade_details.isChecked()
        self._drawing_line_width = dialog.drawing_line_width.value()
        self._rectangle_color = dialog.rectangle_color.text().strip() or "#7B1FA2"
        self._line_color = dialog.line_color.text().strip() or "#7B1FA2"
        self._horizontal_line_color = dialog.horizontal_line_color.text().strip() or "#F57C00"
        self._vertical_line_color = dialog.vertical_line_color.text().strip() or "#00897B"
        self._moving_average_styles = {
            period: (check.isChecked(), color.text().strip() or MOVING_AVERAGE_DEFAULTS[period][1], width.value())
            for period, (check, color, width) in dialog.moving_averages.items()
        }
        for key, value in (
            ("chart_background", self._chart_background), ("ctrl_wheel_zoom_enabled", self._ctrl_wheel_zoom_enabled),
            ("trade_value_threshold_eok", str(self._trade_value_threshold)),
            ("daily_trade_value_threshold_eok", str(self._daily_trade_value_threshold)),
            ("show_trade_details", self._show_trade_details), ("drawing_line_width", str(self._drawing_line_width)),
            ("rectangle_color", self._rectangle_color), ("line_color", self._line_color),
            ("horizontal_line_color", self._horizontal_line_color), ("vertical_line_color", self._vertical_line_color),
        ): self._journal_settings.setValue(key, value)
        for period, (enabled, color, width) in self._moving_average_styles.items():
            self._journal_settings.setValue(f"ma_{period}_enabled", enabled)
            self._journal_settings.setValue(f"ma_{period}_color", color)
            self._journal_settings.setValue(f"ma_{period}_width", str(width))
        for chart in (self._history_chart, self._history_daily_chart):
            chart.set_background(self._chart_background)
            chart.set_ctrl_wheel_zoom_enabled(self._ctrl_wheel_zoom_enabled)
            chart.set_trade_value_threshold(self._trade_value_threshold)
            chart.set_daily_trade_value_threshold(self._daily_trade_value_threshold)
            chart.set_drawing_style(self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color)
            chart.set_moving_average_styles(self._moving_average_styles)
        self._set_trade_details_visible(self._show_trade_details)
        if self._detached_chart_window is not None:
            self._configure_detached_charts(); self._sync_detached_charts()

    def _export_review_image(self) -> None:
        episode = self._active_episode
        if episode is None:
            self._status.setText("이미지로 저장할 매매 묶음을 먼저 선택하세요.")
            return
        summary = episode.summary
        desktop = Path.home() / "Desktop"
        base_dir = desktop if desktop.is_dir() else Path.home()
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", summary.stock_name or summary.stock_code)
        suggested = base_dir / f"{summary.trade_date:%Y%m%d}_{safe_name}_매매복기.png"
        selected, _ = QFileDialog.getSaveFileName(self, "매매 복기 이미지 저장", str(suggested), "PNG 이미지 (*.png)")
        if not selected:
            return
        output = Path(selected)
        if output.suffix.lower() != ".png":
            output = output.with_suffix(".png")

        # 화면 캡처를 확대하지 않고 차트 위젯을 큰 캔버스에 다시 그려 PNG 선명도를 확보한다.
        width, margin, gap = 2000, 44, 24
        content_width = width - margin * 2
        def render_chart(widget: MinuteChart) -> QImage:
            source_width = max(1, widget.width())
            source_height = max(1, widget.height())
            chart_height = max(520, int(source_height * content_width / source_width))
            rendered = QImage(content_width, chart_height, QImage.Format.Format_ARGB32)
            rendered.fill(widget._background)
            chart_painter = QPainter(rendered)
            chart_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            chart_painter.scale(content_width / source_width, chart_height / source_height)
            widget.render(chart_painter, QPoint())
            chart_painter.end()
            return rendered

        current_interval = self._history_interval.currentText()
        chart = render_chart(self._history_chart)
        chart_title = current_interval
        minute_count = self._history_chart.view_count()
        daily_count = self._history_daily_chart.view_count()
        daily_chart_image: QImage | None = None
        daily_missing = False
        if self._dual_history_chart.isChecked():
            if self._history_daily_chart._all_rows:
                daily_chart_image = render_chart(self._history_daily_chart)
            else:
                daily_missing = True

        def document(title: str, body: str, *, prominent: bool = False) -> QTextDocument:
            value = QTextDocument()
            value.setDefaultStyleSheet(
                "body{font-family:'맑은 고딕','Malgun Gothic',sans-serif;font-size:24px;color:#222;line-height:1.35;}"
                "h1{font-size:40px;margin:0 0 12px 0;}h2{font-size:30px;margin:0 0 9px 0;color:#24496e;}"
                ".box{background:#f5f7fa;padding:18px;}"
            )
            heading = "h1" if prominent else "h2"
            escaped = html.escape(body or "—").replace("\n", "<br>")
            value.setHtml(f"<{heading}>{html.escape(title)}</{heading}><div class='box'>{escaped}</div>")
            value.setTextWidth(content_width)
            return value

        title_body = (
            f"{episode.started_at:%Y-%m-%d %H:%M} ~ {episode.ended_at:%Y-%m-%d %H:%M}\n"
            f"매수 {summary.buy_amount:,}원 · 매도 {summary.sell_amount:,}원 · "
            f"실현손익 {summary.realized_profit:+,}원 · 수익률 {summary.return_rate:+.2f}%"
        )
        review = self._repo.load_review(episode.group_id)
        review_body = (
            f"평가: {review.rating} · 상태: {review.status}\n"
            f"매매 이유: {review.reason or '미작성'}\n"
            f"복기 메모: {review.review or '미작성'}\n"
            f"태그: {review.tags or '없음'}"
        )
        title_document = document(summary.stock_name or summary.stock_code, title_body, prominent=True)
        chart_document = document(f"{chart_title} 차트", f"매수·매도 위치와 거래대금 · 저장 봉 수 {minute_count or '전체'}")
        daily_document = (
            document("일봉 차트", "저장된 일봉 데이터가 없어 이번 이미지에는 차트를 넣지 못했습니다.")
            if daily_missing else (document("일봉 차트", f"일봉 가격 흐름과 일별 거래대금 · 저장 봉 수 {daily_count or '전체'}") if daily_chart_image is not None else None)
        )
        detail_documents = (
            document("예상 매매유형", self._setup_label.toPlainText()),
            document("자동 분석", self._analysis_label.toPlainText()),
            document("직접 작성한 복기", review_body),
        )
        documents = tuple(value for value in (title_document, chart_document, daily_document, *detail_documents) if value is not None)
        document_heights = tuple(int(math.ceil(value.size().height())) for value in documents)
        total_height = margin + sum(value + gap for value in document_heights) + chart.height() + gap + margin
        if daily_chart_image is not None:
            total_height += daily_chart_image.height() + gap
        image = QImage(width, max(1, total_height), QImage.Format.Format_ARGB32)
        image.fill(QColor("#FFFFFF"))
        painter = QPainter(image); y = margin

        def draw_document(value: QTextDocument, height: int, top: int) -> int:
            # drawContents의 rect는 배치 좌표가 아니라 문서 내부 클립 영역이므로
            # painter 자체를 이동해야 두 번째 이후 문서가 잘리지 않는다.
            painter.save(); painter.translate(margin, top)
            value.drawContents(painter, QRectF(0, 0, content_width, height))
            painter.restore()
            return top + height + gap

        y = draw_document(title_document, int(math.ceil(title_document.size().height())), y)
        y = draw_document(chart_document, int(math.ceil(chart_document.size().height())), y)
        painter.drawImage(margin, y, chart); y += chart.height() + gap
        if daily_document is not None:
            y = draw_document(daily_document, int(math.ceil(daily_document.size().height())), y)
        if daily_chart_image is not None:
            painter.drawImage(margin, y, daily_chart_image); y += daily_chart_image.height() + gap
        detail_heights = tuple(int(math.ceil(value.size().height())) for value in detail_documents)
        for value, height in zip(detail_documents, detail_heights):
            y = draw_document(value, height, y)
        painter.end()
        if image.save(str(output), "PNG"):
            self._status.setText(f"복기 이미지 저장 완료 · {output.name}")
        else:
            self._status.setText("복기 이미지를 저장하지 못했습니다.")

    def set_stock(self, code: str, name: str) -> None:
        if not code:
            return
        if code != self._live_code:
            self._chart.clear_annotations()
        self._live_code, self._live_name = code, name
        self._chart.clear_daily_rows()
        now = datetime.now(); self._repo.remember_stock(code, name, now)
        self._title.setText(f"{name} ({code}) · 장중 매매일지")
        self.refresh()
        chart_rows = self._repo.load_chart_bars(code, now)
        if len({str(row[0])[:10] for row in chart_rows}) < 2 and (self._worker is None or not self._worker.isRunning()):
            self._start_bar_confirmation(code, date.today(), include_previous=True)
        if self._live_interval.currentText() == "일봉":
            self._start_daily_chart(code, date.today(), "live")

    def sync_history(self) -> None:
        if self._history_worker is not None and self._history_worker.isRunning():
            return
        start, end = self._from_date.date().toPython(), self._to_date.date().toPython()
        if start > end:
            start, end = end, start
        if (end - start).days > 31:
            self._status.setText("한 번에 최대 31일까지 조회할 수 있습니다."); return
        worker = HistoryWorker(self._history_service, self._cost_service, start, end)
        worker.progress.connect(self._status.setText); worker.failed.connect(lambda message: self._status.setText(f"조회 실패 · {message}"))
        worker.completed.connect(self._history_received); worker.finished.connect(self._history_finished); worker.finished.connect(worker.deleteLater)
        self._history_worker = worker; worker.start()

    def _auto_sync_history_once(self) -> bool:
        if not self._auto_history_sync_enabled or not self._api_ready:
            return False
        # NXT 애프터마켓 체결까지 포함하려면 당일 전체 체결은 20:05 이후에 확정한다.
        now = datetime.now()
        if now.time() < time(20, 5):
            return False
        if self._journal_settings.value("last_history_sync_day", "", type=str) == date.today().isoformat():
            return False
        self._status.setText("오늘 첫 실행 · 키움 체결내역 자동 확인 중…")
        self.sync_history()
        return self._history_worker is not None and self._history_worker.isRunning()

    def _history_received(self, result: object, start: object, end: object) -> None:
        if isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], tuple) and isinstance(result[1], tuple) and isinstance(start, date) and isinstance(end, date):
            fills, costs, cost_error = result
            self._repo.upsert_history_sync(fills, costs)
            if datetime.now().time() >= time(20, 5):
                self._journal_settings.setValue("last_history_sync_day", date.today().isoformat())
            self._status.setText(
                f"체결 {len(fills)}건 저장 · 비용조회 실패(다음에 재시도)" if cost_error
                else f"조회 완료 · 체결 {len(fills)}건 · 실제비용 {len(costs)}묶음"
            )
            self.reload_history()

    def _history_finished(self) -> None:
        self._history_worker = None
        if self._quit_after_history_sync:
            self._quit_after_history_sync = False
            self.shutdown()
            app = QApplication.instance()
            if app is not None:
                app.setProperty("journal_terminating", True)
                app.exit(0)

    def reload_history(self) -> None:
        start = datetime.combine(self._from_date.date().toPython(), time())
        end = datetime.combine(self._to_date.date().toPython() + timedelta(days=1), time())
        result = self._trade_history_query.load(start, end)
        self._visible_fills = result.fills
        self._visible_costs = result.costs
        self._all_episodes = result.episodes
        self._apply_history_filters()
        if not self._episodes:
            self._render_fills(result.fills)
        self._refresh_backfill_table()

    def _apply_history_filters(self, *args: object) -> None:
        query = self._stock_filter.text().strip().lower() if hasattr(self, "_stock_filter") else ""
        result_filter = self._result_filter.currentText() if hasattr(self, "_result_filter") else "전체 손익"
        review_filter = self._review_filter.currentText() if hasattr(self, "_review_filter") else "전체 복기"
        self._episodes = self._trade_history_query.filter(
            self._all_episodes,
            query=query,
            result_filter=result_filter,
            review_filter=review_filter,
        )
        self._render_summaries()
        self._refresh_statistics()

    def _render_summaries(self) -> None:
        selected_group_id = self._active_episode.group_id if self._active_episode is not None else ""
        self._summary_table.blockSignals(True)
        self._summary_table.clearSelection(); self._summary_table.setCurrentItem(None)
        delegate = self._summary_table.itemDelegate()
        if isinstance(delegate, JournalCellMarkerDelegate):
            delegate.set_selected_cell(None)
        group_ids = tuple(episode.group_id for episode in self._episodes)
        setups = self._repo.load_trade_setups(group_ids)
        cycle_overrides = self._repo.load_trade_setup_cycle_overrides_many(group_ids)
        reviews = self._repo.load_reviews(group_ids)
        backfill_candidates = tuple(dict.fromkeys(
            (fill.stock_code, fill.filled_at.date())
            for episode in self._episodes for fill in episode.fills
        ))
        backfill_states = self._repo.bar_backfill_states(backfill_candidates)
        episode_displays = {
            episode.group_id: TradeHistoryEpisodeDisplay(
                setup=setups.get(episode.group_id),
                cycle_overrides=cycle_overrides.get(episode.group_id, {}),
                review_status=reviews.get(episode.group_id, TradeReview(episode.group_id)).status,
                bar_state=self._episode_bar_state(episode, backfill_states),
            )
            for episode in self._episodes
        }
        model = build_trade_history_summary_view_model(
            episodes=self._episodes,
            visible_fills=self._visible_fills,
            visible_costs=self._visible_costs,
            selected_start=self._from_date.date().toPython(),
            selected_end=self._to_date.date().toPython(),
            estimated_buy_cost_rate=self._estimated_buy_cost_rate,
            estimated_sell_cost_rate=self._estimated_sell_cost_rate,
            episode_displays=episode_displays,
        )
        self._period_summary.setText(model.period_summary_text)
        self._summary_table.setRowCount(len(model.rows))
        for row, row_model in enumerate(model.rows):
            for column, value in enumerate(row_model.values):
                item = QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole, row_model.episode)
                if column in (6, 7, 9):
                    item.setForeground(QBrush(QColor(row_model.profit_color)))
                self._summary_table.setItem(row, column, item)
        self._summary_table.blockSignals(False)
        self._summary_table.resizeColumnsToContents()
        if self._episodes:
            selected_row = next(
                (row for row, episode in enumerate(self._episodes) if episode.group_id == selected_group_id),
                0,
            )
            self._summary_table.blockSignals(True)
            self._summary_table.selectRow(selected_row); self._summary_table.setCurrentCell(selected_row, 0)
            self._summary_table.blockSignals(False)
            self._select_journal_cell(self._summary_table, selected_row, 0)
            # selectRow()가 currentCell 설정 전에 selectionChanged를 내보내면 분석 호출이
            # 빈 선택으로 끝날 수 있으므로, 최종 선택이 완성된 뒤 명시적으로 갱신한다.
            self._show_selected_summary()

    def _render_fills(self, fills: tuple[TradeFill, ...]) -> None:
        self._fills_table.blockSignals(True)
        self._fills_table.clearSelection(); self._fills_table.setCurrentItem(None)
        delegate = self._fills_table.itemDelegate()
        if isinstance(delegate, JournalCellMarkerDelegate):
            delegate.set_selected_cell(None)
        self._fills_table.setRowCount(len(fills))
        for row, fill in enumerate(fills):
            amount = fill.quantity * fill.price
            values = (fill.filled_at.strftime("%Y-%m-%d %H:%M:%S"), fill.stock_name or fill.stock_code, fill.side,
                      f"{fill.quantity:,}", f"{fill.price:,}", f"{amount:,}", fill.order_type, fill.market, fill.order_no)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, fill)
                self._fills_table.setItem(row, column, item)
        self._fills_table.blockSignals(False)
        self._fills_table.resizeColumnsToContents()
        if fills:
            self._fills_table.selectRow(0); self._fills_table.setCurrentCell(0, 0)
            self._select_journal_cell(self._fills_table, 0, 0)

    def _episode_bar_state(
        self, episode: TradeEpisode,
        states: dict[tuple[str, date], BarBackfillState] | None = None,
    ) -> str:
        tasks = sorted({(fill.stock_code, fill.filled_at.date()) for fill in episode.fills}, key=lambda value: value[1])
        loaded = states if states is not None else self._repo.bar_backfill_states(tuple(tasks))
        return summarize_episode_bar_state(tuple(loaded[task].state for task in tasks))

    def _backfill_tasks(self, *, failed_only: bool = False, all_saved: bool = False) -> tuple[tuple[str, date], ...]:
        cutoff = history_backfill_cutoff(datetime.now())
        candidates = (
            self._repo.bar_backfill_candidates(cutoff) if all_saved else
            tuple(sorted({(fill.stock_code, fill.filled_at.date()) for fill in self._visible_fills if fill.filled_at.date() < cutoff}, key=lambda value: (value[1], value[0])))
        )
        states = {
            candidate: value.state
            for candidate, value in self._repo.bar_backfill_states(candidates).items()
        }
        return select_history_backfill_tasks(candidates, states, failed_only=failed_only)

    def _start_missing_backfill(
        self, *, failed_only: bool = False, tasks: tuple[tuple[str, date], ...] | None = None,
        automatic: bool = False,
    ) -> None:
        if self._backfill_worker is not None and self._backfill_worker.isRunning():
            self._status.setText("분봉 보완이 이미 진행 중입니다."); return
        if self._history_worker is not None and self._history_worker.isRunning():
            self._status.setText("체결내역 조회가 끝난 뒤 분봉을 보완하세요."); return
        tasks = tasks if tasks is not None else self._backfill_tasks(failed_only=failed_only)
        if not tasks:
            self._status.setText("재시도할 실패 분봉이 없습니다." if failed_only else "보완할 누락 분봉이 없습니다."); return
        worker = BackfillWorker(self._minute_service, tasks)
        worker.progress.connect(self._status.setText)
        worker.item_completed.connect(self._backfill_received)
        worker.item_failed.connect(self._backfill_failed)
        worker.completed.connect(self._backfill_completed)
        worker.finished.connect(self._backfill_finished); worker.finished.connect(worker.deleteLater)
        self._automatic_backfill_running = automatic
        self._backfill_worker = worker; worker.start()

    def _save_auto_backfill_setting(self, enabled: bool) -> None:
        QSettings("KiwoomMonitor", "TradingJournalWindow").setValue("auto_backfill", enabled)
        if enabled:
            self._automatic_backfill_attempted_for = None
            self._check_automatic_backfill()

    def _check_automatic_backfill(self) -> None:
        if not self._auto_backfill.isChecked() or self._backfill_worker is not None or self._history_worker is not None:
            return
        today = date.today()
        attempt_key = (today, datetime.now().hour >= 20)
        if self._automatic_backfill_attempted_for == attempt_key:
            return
        tasks = self._backfill_tasks(all_saved=True)
        self._automatic_backfill_attempted_for = attempt_key
        if not tasks:
            return
        batch = tasks[:self._AUTO_BACKFILL_BATCH_SIZE]
        self._status.setText(f"자동 분봉 보완 준비 · {len(batch)}건" + (f" / 남은 {len(tasks)}건" if len(tasks) > len(batch) else ""))
        self._start_missing_backfill(tasks=batch, automatic=True)

    def _backfill_received(self, code: str, day: object, bars: object) -> None:
        if not isinstance(day, date) or not isinstance(bars, tuple):
            return
        if not self._repo.save_bar_backfill_result(code, day, bars, datetime.now()):
            return
        if backfill_affects_history_selection(
            code,
            day,
            selected_code=self._selected_history_code,
            selected_range=self._selected_history_range,
        ):
            self._refresh_selected_history_after_bars(code, analyze_any_active=True)

    def _backfill_failed(self, code: str, day: object, message: str) -> None:
        if isinstance(day, date):
            self._repo.mark_bar_backfill(code, day, "실패", message)

    def _backfill_completed(self, success: int, failed: int) -> None:
        prefix = "자동 " if self._automatic_backfill_running else ""
        self._status.setText(f"{prefix}분봉 보완 완료 · 성공 {success}건 · 실패 {failed}건")
        self._render_summaries()
        self._refresh_backfill_table()

    def _backfill_finished(self) -> None:
        self._backfill_worker = None
        self._automatic_backfill_running = False

    def _stop_backfill(self) -> None:
        if self._backfill_worker is not None and self._backfill_worker.isRunning():
            self._backfill_worker.requestInterruption(); self._status.setText("현재 요청이 끝난 뒤 분봉 보완을 중지합니다.")

    def _refresh_backfill_table(self, *args: object) -> None:
        if not hasattr(self, "_backfill_table"):
            return
        states = self._repo.list_bar_backfill_states(date.today() + timedelta(days=1))
        selected = self._backfill_filter.currentText()
        if selected != "전체":
            states = tuple(value for value in states if value.state == selected)
        self._backfill_table.setRowCount(len(states))
        for row, state in enumerate(states):
            values = (state.trade_date.isoformat(), state.stock_code, state.state, f"{state.bar_count}개", state.message)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, (state.stock_code, state.trade_date))
                if column == 2:
                    colors = {"확정": "#2E7D32", "실패": "#D32F2F", "일부": "#EF6C00", "미조회": "#555555"}
                    item.setForeground(QBrush(QColor(colors.get(state.state, "#555555"))))
                self._backfill_table.setItem(row, column, item)
        self._backfill_table.resizeColumnsToContents()

    def _retry_selected_backfills(self) -> None:
        rows = sorted({index.row() for index in self._backfill_table.selectionModel().selectedRows()})
        tasks: list[tuple[str, date]] = []
        for row in rows:
            item = self._backfill_table.item(row, 0)
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], date):
                tasks.append(value)
        if not tasks:
            self._status.setText("재조회할 분봉 항목을 선택하세요."); return
        self._start_missing_backfill(tasks=tuple(tasks))

    def _refresh_statistics(self, *args: object) -> None:
        if not hasattr(self, "_stats_table"):
            return
        statistics = summarize_periods(self._episodes, self._stats_unit.currentText())
        total_profit = sum(value.realized_profit for value in statistics)
        trades = sum(value.trade_count for value in statistics)
        wins = sum(value.win_count for value in statistics)
        losses = sum(value.loss_count for value in statistics)
        closed = wins + losses
        tag_counts: dict[str, int] = {}
        rating_counts: dict[str, int] = {}
        for episode in self._episodes:
            review = self._repo.load_review(episode.group_id)
            rating_counts[review.rating] = rating_counts.get(review.rating, 0) + 1
            for tag in review.tags.replace("#", "").split(","):
                normalized = tag.strip()
                if normalized:
                    tag_counts[normalized] = tag_counts.get(normalized, 0) + 1
        common_tags = ", ".join(
            f"{name} {count}회" for name, count in sorted(tag_counts.items(), key=lambda value: (-value[1], value[0]))[:5]
        ) or "아직 복기 태그 없음"
        weak_count = rating_counts.get("아쉬움", 0)
        self._stats_summary.setText(
            f"현재 검색 범위 · 매매 {trades}건 · 수익 {wins}건 · 손실 {losses}건 · "
            f"승률 {(wins / closed * 100 if closed else 0):.1f}% · 실현손익 {total_profit:+,}원\n"
            f"반복 태그: {common_tags} · 아쉬움 평가 {weak_count}건"
        )
        self._stats_table.setRowCount(len(statistics))
        for row, value in enumerate(statistics):
            cells = (
                value.period, f"{value.trade_count}건", f"{value.win_count}건", f"{value.loss_count}건",
                f"{value.win_rate:.1f}%", f"{value.realized_profit:+,}원", f"{value.return_rate:+.2f}%",
            )
            for column, text_value in enumerate(cells):
                item = QTableWidgetItem(text_value)
                if column in (5, 6):
                    item.setForeground(QBrush(QColor("#D32F2F" if value.realized_profit > 0 else ("#1976D2" if value.realized_profit < 0 else "#555555"))))
                self._stats_table.setItem(row, column, item)
        self._stats_table.resizeColumnsToContents()

    def _show_selected_summary(self) -> None:
        row = self._summary_table.currentRow()
        item = self._summary_table.item(row, 0) if row >= 0 else None
        episode = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(episode, TradeEpisode):
            return
        if self._active_episode is not None and self._active_episode.group_id != episode.group_id:
            self._save_active_review()
        self._active_episode = episode
        self._load_active_review()
        summary = episode.summary
        selection = trade_episode_selection(episode)
        related = selection.fills
        self._selected_history_code = selection.stock_code
        self._selected_history_day = selection.selected_day
        self._selected_history_focus = selection.focus
        self._selected_history_range = selection.date_range
        self._history_chart.clear_daily_rows()
        self._history_daily_chart.clear_daily_rows()
        self._history_chart.set_fills(tuple(sorted(related, key=lambda value: value.filled_at)))
        self._history_daily_chart.set_fills(tuple(sorted(related, key=lambda value: value.filled_at)))
        chart_rows = self._load_selected_chart_rows()
        self._history_chart.set_rows(chart_rows)
        self._apply_history_chart_state(self._history_interval.currentText(), self._history_chart)
        self._render_fills(related)
        if related and chart_rows:
            self._history_chart.focus_time(related[0].filled_at)
        self._show_trade_analysis(episode, chart_rows)
        self._sync_detached_charts()
        self._request_incomplete_trade_days(related)
        self._request_previous_chart_day(summary.stock_code, summary.trade_date)
        if self._history_interval.currentText() == "일봉" or self._dual_history_chart.isChecked():
            # 일봉의 끝은 매매 종료일이 아니라 현재 조회 가능한 최신 거래일이다.
            # 매매일은 아래 focus_time의 기준으로만 사용한다.
            self._start_daily_chart(summary.stock_code, date.today(), "history")

    def _request_previous_chart_day(self, code: str, day: date) -> None:
        """분봉 상단의 전일 종가와 연속 차트를 위해 직전 거래일을 한 번만 보완한다."""
        key = (code, day)
        if key in self._previous_chart_requested:
            return
        rows = self._repo.load_chart_bars(code, datetime.combine(day, time()))
        if any(datetime.fromisoformat(str(row[0])).date() < day for row in rows):
            self._previous_chart_requested.add(key)
            return
        if self._worker is not None and self._worker.isRunning():
            QTimer.singleShot(
                1_000,
                lambda target_code=code, target_day=day: self._request_previous_chart_day(target_code, target_day),
            )
            return
        self._previous_chart_requested.add(key)
        self._status.setText(f"{code} 직전 거래일 분봉 보완 중…")
        self._start_bar_confirmation(code, day, include_previous=True)

    @staticmethod
    def _sync_chart_scrollbar(scrollbar: QScrollBar, maximum: int, current: int, page: int) -> None:
        scrollbar.blockSignals(True)
        scrollbar.setRange(0, maximum); scrollbar.setPageStep(max(1, page)); scrollbar.setValue(current)
        scrollbar.setVisible(maximum > 0)
        scrollbar.blockSignals(False)

    @staticmethod
    def _sync_view_count_input(control: QSpinBox, count: int) -> None:
        control.blockSignals(True)
        control.setValue(max(0, int(count)))
        control.blockSignals(False)

    def _history_source_id(self) -> str:
        code = self._selected_history_code or (self._active_episode.summary.stock_code if self._active_episode is not None else "")
        return f"stock:{code}" if code else ""

    def _history_annotations_changed(self, interval: str, values: object) -> None:
        if self._applying_shared_chart_state:
            return
        source_id = self._history_source_id()
        if not source_id:
            return
        normalized = tuple(tuple(value) for value in values) if isinstance(values, (tuple, list)) else ()
        self._shared_chart_annotations[(source_id, interval)] = normalized
        if self._detached_chart_window is not None:
            chart = self._history_daily_chart if interval == "일봉" else self._history_chart
            self._detached_chart_window.set_shared_chart_state(source_id, interval, chart.view_count(), normalized)

    def _history_view_count_changed(self, count: int) -> None:
        self._sync_view_count_input(self._history_view_count, count)
        interval = self._history_interval.currentText()
        self._journal_settings.setValue(f"history_chart_view_count_{interval}", max(0, int(count)))
        self._history_annotations_changed(interval, self._history_chart.annotations())

    def _daily_history_view_count_changed(self, count: int) -> None:
        self._journal_settings.setValue("history_chart_view_count_일봉", max(0, int(count)))
        self._history_annotations_changed("일봉", self._history_daily_chart.annotations())

    def _apply_history_chart_state(self, interval: str, chart: MinuteChart) -> None:
        source_id = self._history_source_id()
        count = self._journal_settings.value(f"history_chart_view_count_{interval}", 120, type=int)
        annotations = self._shared_chart_annotations.get((source_id, interval), ())
        self._applying_shared_chart_state = True
        try:
            chart.set_view_count(count)
            chart.set_annotations(annotations)
            if chart is self._history_chart:
                self._sync_view_count_input(self._history_view_count, count)
        finally:
            self._applying_shared_chart_state = False
        if self._detached_chart_window is not None and source_id:
            self._detached_chart_window.set_shared_chart_state(source_id, interval, count, annotations)

    def _detached_chart_state_changed(self, source_id: str, interval: str, count: int, annotations: object) -> None:
        normalized = tuple(tuple(value) for value in annotations) if isinstance(annotations, (tuple, list)) else ()
        self._shared_chart_annotations[(source_id, interval)] = normalized
        if source_id != self._history_source_id():
            return
        self._journal_settings.setValue(f"history_chart_view_count_{interval}", max(0, int(count)))
        target = self._history_daily_chart if interval == "일봉" and self._dual_history_chart.isChecked() else (
            self._history_chart if interval == self._history_interval.currentText() else None
        )
        if target is None:
            return
        self._applying_shared_chart_state = True
        try:
            target.set_view_count(count); target.set_annotations(normalized)
            if target is self._history_chart:
                self._sync_view_count_input(self._history_view_count, count)
        finally:
            self._applying_shared_chart_state = False

    def _history_interval_changed(self, interval: str) -> None:
        self._journal_settings.setValue("history_chart_interval", interval)
        self._history_chart.set_interval(interval)
        self._apply_history_chart_state(interval, self._history_chart)
        self._focus_selected_history()
        if interval == "일봉" and self._selected_history_code and self._selected_history_day is not None:
            self._start_daily_chart(self._selected_history_code, date.today(), "history")

    def _toggle_dual_history_chart(self, enabled: bool) -> None:
        self._show_dual_history_chart = bool(enabled)
        self._journal_settings.setValue("show_dual_history_chart", self._show_dual_history_chart)
        self._history_daily_title.setVisible(enabled); self._history_daily_chart.setVisible(enabled)
        if not enabled:
            return
        if self._history_interval.currentText() == "일봉":
            self._history_interval.setCurrentText("1분")
        if self._active_episode is not None:
            self._history_daily_chart.set_fills(self._active_episode.fills)
            self._history_daily_chart.set_setup_types(self._history_chart.setup_types())
        self._apply_history_chart_state("일봉", self._history_daily_chart)
        if self._selected_history_code and self._selected_history_day is not None:
            self._start_daily_chart(self._selected_history_code, date.today(), "history")

    def _open_detached_charts(self) -> None:
        if self._detached_chart_window is None:
            self._detached_chart_window = DetachedChartWindow(self._journal_settings, self)
            self._detached_chart_window.set_stock_choices(self._repo.load_monitor_stock_catalog(self._monitor_db))
            self._detached_chart_window.source_changed.connect(self._detached_source_changed)
            self._detached_chart_window.chart_state_changed.connect(self._detached_chart_state_changed)
        source_id = self._history_source_id()
        if source_id:
            self._detached_chart_window.set_shared_chart_state(
                source_id, self._history_interval.currentText(), self._history_chart.view_count(), self._history_chart.annotations(),
            )
            self._detached_chart_window.set_shared_chart_state(
                source_id, "일봉", self._history_daily_chart.view_count(), self._history_daily_chart.annotations(),
            )
        self._configure_detached_charts(); self._sync_detached_charts()
        self._detached_chart_window.show(); self._detached_chart_window.raise_(); self._detached_chart_window.activateWindow()
        if self._selected_history_code and self._selected_history_day is not None:
            self._start_daily_chart(self._selected_history_code, date.today(), "history")

    def _configure_detached_charts(self) -> None:
        if self._detached_chart_window is None: return
        self._detached_chart_window.configure(
            self._chart_background, self._ctrl_wheel_zoom_enabled, self._trade_value_threshold,
            self._daily_trade_value_threshold, self._show_trade_details, self._drawing_line_width,
            self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color,
        )
        for panel in self._detached_chart_window.panels:
            panel.chart.set_moving_average_styles(self._moving_average_styles)

    def _sync_detached_charts(self) -> None:
        if self._detached_chart_window is None: return
        episode = self._active_episode
        if episode is None:
            self._detached_chart_window.set_data(None, (), (), ()); return
        minute_rows = self._load_selected_chart_rows()
        latest_day = date.today()
        daily_rows = self._repo.load_daily_bars(episode.summary.stock_code, latest_day)
        custom_rows: dict[str, tuple[tuple[object, ...], ...]] = {}
        for code in self._detached_chart_window.selected_stock_codes():
            try:
                self._repo.import_live_bars(self._monitor_db, code, datetime.combine(episode.ended_at.date(), time()))
            except (OSError, sqlite3.Error):
                pass
            custom_rows[code] = self._repo.load_chart_bars_range(code, episode.started_at.date(), episode.ended_at.date())
            try: self._repo.import_monitor_daily_bars(self._monitor_db, code, latest_day, 250)
            except (OSError, sqlite3.Error): pass
            custom_rows[f"daily:{code}"] = self._repo.load_daily_bars(code, latest_day)
        index_rows = {
            market: self._repo.load_monitor_market_index_bars(
                self._monitor_db, market, episode.started_at.date(), episode.ended_at.date(),
            )
            for market in ("kospi", "kosdaq")
        }
        for market in ("kospi", "kosdaq"):
            index_rows[f"daily:{market}"] = self._repo.load_monitor_market_index_daily(
                self._monitor_db, market, latest_day,
            )
        index_rows.update(custom_rows)
        self._detached_chart_window.set_data(
            episode, minute_rows, daily_rows, self._history_chart.setup_types(), index_rows,
        )
        if self._detached_chart_window.selected_index_markets():
            self._start_market_index_backfill(episode.ended_at.date())

    def _detached_source_changed(self) -> None:
        self._sync_detached_charts()
        if self._detached_chart_window is None or self._active_episode is None: return
        day = self._active_episode.ended_at.date()
        for code in self._detached_chart_window.selected_stock_codes():
            key = (code, day)
            if key in self._compare_stock_requested:
                rows = self._repo.load_chart_bars_range(
                    code, self._active_episode.started_at.date(), day,
                )
                if rows or (self._worker is not None and self._worker.isRunning()):
                    continue
                # 이전 조회가 빈 응답/실패로 끝났다면 사용자가 다시 선택했을 때 재시도한다.
                self._compare_stock_requested.discard(key)
            self._start_daily_chart(code, date.today(), f"detached:{code}")
            if self._worker is None or not self._worker.isRunning():
                self._compare_stock_requested.add(key)
                self._status.setText(f"비교 종목 분봉 보완 중 · {code}")
                self._start_bar_confirmation(code, day, include_previous=True)
            else:
                QTimer.singleShot(1_000, self._detached_source_changed)
            break

    def _start_market_index_backfill(self, day: date) -> None:
        if day in self._market_index_requested_days:
            return
        if self._market_index_worker is not None and self._market_index_worker.isRunning():
            if day != self._market_index_active_day:
                self._pending_market_index_day = day
            return
        self._market_index_active_day = day
        worker = MarketIndexBackfillWorker(self._market_index_service, day)
        worker.completed.connect(self._market_index_backfill_completed)
        worker.failed.connect(self._market_index_backfill_failed)
        worker.finished.connect(self._market_index_backfill_finished)
        worker.finished.connect(worker.deleteLater)
        self._market_index_worker = worker
        worker.start()

    def _market_index_backfill_completed(self, rows: object) -> None:
        if isinstance(rows, tuple) and len(rows) == 2 and isinstance(rows[0], dict) and isinstance(rows[1], dict):
            try:
                MinuteBarRepository(self._monitor_db).replace_market_index_history(rows[0], rows[1])
            except Exception as error: self._status.setText(f"시장지수 저장 실패 · {error}"); return
            if self._market_index_active_day is not None:
                self._market_index_requested_days.add(self._market_index_active_day)
            self._status.setText(f"코스피·코스닥 분봉·일봉 보완 · {len(rows[0])}개")
            self._sync_detached_charts()

    def _market_index_backfill_failed(self, message: str) -> None:
        self._status.setText(f"시장지수 보완 실패 · {message}")

    def _market_index_backfill_finished(self) -> None:
        self._market_index_worker = None
        self._market_index_active_day = None
        pending, self._pending_market_index_day = self._pending_market_index_day, None
        if pending is not None:
            self._start_market_index_backfill(pending)

    def _detached_chart_export_context(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        """전용 차트의 현재 구성 이미지에 붙일 복기 내용을 반환한다."""
        episode = self._active_episode
        if episode is None:
            return ("매매일지 차트", ())
        summary = episode.summary
        review = self._repo.load_review(episode.group_id)
        review_body = (
            f"평가: {review.rating} · 상태: {review.status}\n"
            f"매매 이유: {review.reason or '미작성'}\n"
            f"복기 메모: {review.review or '미작성'}\n"
            f"태그: {review.tags or '없음'}"
        )
        name = summary.stock_name or summary.stock_code
        title = f"{episode.started_at:%Y%m%d}_{name}_매매복기"
        return (
            title,
            (
                ("예상 매매유형", self._setup_label.toPlainText()),
                ("자동 분석", self._analysis_label.toPlainText()),
                ("직접 작성한 복기", review_body),
            ),
        )

    def _live_interval_changed(self, interval: str) -> None:
        self._chart.set_interval(interval)
        if interval == "일봉" and self._live_code:
            self._start_daily_chart(self._live_code, date.today(), "live")

    def _start_daily_chart(self, code: str, day: date, target: str) -> None:
        request = (code, day, target)
        try:
            self._repo.import_monitor_daily_bars(self._monitor_db, code, day, 250)
        except (OSError, sqlite3.Error):
            pass
        cached = self._repo.load_daily_bars(code, day)
        if cached:
            self._apply_daily_chart_rows(code, cached, target, refresh_analysis=False)
            # 과거 복기용 일봉이 충분히 저장돼 있으면 같은 API를 반복 호출하지 않는다.
            if should_reuse_cached_daily_chart(target, day, len(cached), today=date.today()):
                self._status.setText(f"저장된 일봉 {len(cached)}개 표시")
                return
        if self._daily_chart_worker is not None and self._daily_chart_worker.isRunning():
            self._pending_daily_chart = request
            return
        self._pending_daily_chart = None
        worker = DailyChartWorker(self._daily_chart_service, code, day, target)
        worker.completed.connect(self._daily_chart_received)
        worker.failed.connect(lambda message: self._status.setText(f"일봉 조회 실패 · {message}"))
        worker.finished.connect(self._daily_chart_finished); worker.finished.connect(worker.deleteLater)
        self._daily_chart_worker = worker; self._status.setText("최근 일봉 가져오는 중…"); worker.start()

    def _daily_chart_received(self, code: str, rows: object, target: str) -> None:
        if not isinstance(rows, tuple):
            return
        self._repo.upsert_daily_bars(code, rows)
        self._apply_daily_chart_rows(code, rows, target, refresh_analysis=True)
        self._status.setText(f"일봉 {len(rows)}개 확인")

    def _apply_daily_chart_rows(
        self,
        code: str,
        rows: tuple[tuple[object, ...], ...],
        target: str,
        *,
        refresh_analysis: bool,
    ) -> None:
        display_target = daily_chart_display_target(
            target,
            code,
            selected_history_code=self._selected_history_code,
            live_code=self._live_code,
        )
        if display_target == "history":
            self._history_chart.set_daily_rows(rows)
            self._history_daily_chart.set_daily_rows(rows)
            apply_shared_state = getattr(self, "_apply_history_chart_state", None)
            if callable(apply_shared_state):
                apply_shared_state("일봉", self._history_daily_chart)
            self._focus_selected_history()
            self._sync_detached_charts()
            if (
                refresh_analysis
                and self._active_episode is not None
                and self._active_episode.summary.stock_code == code
            ):
                self._show_trade_analysis(self._active_episode, self._load_selected_chart_rows())
        elif display_target == "live":
            self._chart.set_daily_rows(rows)
        elif display_target == "detached":
            self._sync_detached_charts()

    def _daily_chart_finished(self) -> None:
        self._daily_chart_worker = None
        pending, self._pending_daily_chart = self._pending_daily_chart, None
        if pending is not None:
            self._start_daily_chart(*pending)

    def _show_trade_analysis(self, episode: TradeEpisode, rows: tuple[tuple[object, ...], ...]) -> None:
        self._journal_news_button.setEnabled(True)
        prepared = self._trade_analysis_preparer.prepare(
            episode,
            rows,
            active_pack=self._active_strategy_pack,
            strategy_packs=self._strategy_packs,
            result_mode=self._strategy_result_mode,
        )
        self._journal_news_button.setText(f"관련 뉴스 보기 · 고정 {len(prepared.linked_news)}건")
        analyses = analyze_trade_cycles(
            prepared.cycles, rows,
            personal_rules=self._load_personal_rules(),
            trade_value_threshold_eok=self._trade_value_threshold,
            selected_types=prepared.type_selection.selected_types,
            cycle_setups=prepared.cycle_setups,
            overrides=prepared.type_selection.override_map(),
            entry_snapshots=prepared.entry_snapshots,
            manual_unverifiable_for=self._active_strategy_pack.unverifiable_for,
        )
        view_model = build_trade_analysis_view_model(
            overall_setup=prepared.overall_setup,
            pack_results=prepared.pack_results,
            type_selection=prepared.type_selection,
            cycles=prepared.cycles,
            cycle_setups=prepared.cycle_setups,
            analyses=analyses,
            entry_snapshots=prepared.entry_snapshots,
            linked_news=prepared.linked_news,
            active_pack=self._active_strategy_pack, packs=self._strategy_packs,
            structured_rules=self._structured_personal_rules,
            personal_rule_count=len(self._load_personal_rules()),
            draft_rule_counts=prepared.draft_rule_counts,
            total_return_rate=episode.summary.return_rate,
        )
        self._setup_label.setPlainText(view_model.setup_summary_text)
        self._history_chart.set_setup_types(view_model.selected_types)
        self._history_daily_chart.set_setup_types(view_model.selected_types)
        self._sync_detached_charts()
        self._loading_setup = True
        self._setup_cycles.setRowCount(len(view_model.cycle_rows))
        for row_model in view_model.cycle_rows:
            row_index = row_model.cycle_number - 1
            self._setup_cycles.setItem(row_index, 0, QTableWidgetItem(f"{row_model.cycle_number}차"))
            self._setup_cycles.setItem(row_index, 1, QTableWidgetItem(row_model.automatic_type))
            self._setup_cycles.setItem(row_index, 2, QTableWidgetItem(row_model.subtype))
            self._setup_cycles.setItem(row_index, 3, QTableWidgetItem(row_model.confidence_text))
            editor = QComboBox()
            editor.addItem("자동 판정 사용")
            editor.addItems(view_model.available_types)
            editor.setCurrentText(row_model.override_value)
            editor.currentTextChanged.connect(
                lambda value, cycle_index=row_index: self._setup_cycle_override_changed(cycle_index, value)
            )
            self._setup_cycles.setCellWidget(row_index, 4, editor)
        self._setup_cycles.resizeColumnsToContents()
        row_height = self._setup_cycles.verticalHeader().defaultSectionSize()
        header_height = self._setup_cycles.horizontalHeader().height()
        self._setup_cycles.setFixedHeight(min(260, header_height + row_height * max(1, len(view_model.cycle_rows)) + 6))
        self._loading_setup = False
        row = self._summary_table.currentRow()
        if row >= 0:
            self._summary_table.setItem(row, 14, QTableWidgetItem(prepared.type_selection.selected_type_label))
        self._analysis_data_status.setText(view_model.data_status_text)
        self._analysis_label.setPlainText(view_model.analysis_text)

    def _open_active_news(self) -> None:
        episode = self._active_episode
        if episode is None:
            return
        document = {
            "code": episode.summary.stock_code,
            "name": episode.summary.stock_name,
            "group_id": episode.group_id,
            "trade_date": episode.summary.trade_date.isoformat(),
        }
        try:
            self._journal_news_channel.send(document)
            self._review_saved.setText("뉴스창에서 대표 기사를 선택해 매매일지에 추가할 수 있습니다.")
        except OSError as error:
            self._review_saved.setText(f"뉴스창 요청 실패: {error}")

    def _load_personal_rules(self) -> tuple[str, ...]:
        return self._personal_rules

    def _setup_cycle_override_changed(self, cycle_index: int, value: str) -> None:
        if self._loading_setup or self._active_episode is None:
            return
        manual_type = "" if value == "자동 판정 사용" else value
        self._repo.save_trade_setup_cycle_override(self._active_episode.group_id, cycle_index, manual_type)
        rows = self._load_selected_chart_rows()
        self._show_trade_analysis(self._active_episode, rows)

    def _load_active_review(self) -> None:
        if self._active_episode is None:
            return
        review = self._repo.load_review(self._active_episode.group_id)
        self._loading_review = True
        self._review_reason.setPlainText(review.reason); self._review_note.setPlainText(review.review)
        self._review_tags.setText(review.tags); self._review_rating.setCurrentText(review.rating or "보통")
        self._review_status.setCurrentText(review.status or "미작성")
        self._loading_review = False
        self._review_saved.setText("저장된 복기" if any((review.reason, review.review, review.tags)) else "아직 작성하지 않음")

    def _review_changed(self, *args: object) -> None:
        if self._loading_review or self._active_episode is None:
            return
        self._review_saved.setText("저장 대기…")
        self._review_save_timer.start(700)

    def _save_active_review(self) -> None:
        if self._active_episode is None or self._loading_review:
            return
        self._review_save_timer.stop()
        review = TradeReview(
            self._active_episode.group_id, self._review_reason.toPlainText().strip(),
            self._review_note.toPlainText().strip(), self._review_tags.text().strip(),
            self._review_rating.currentText(), self._review_status.currentText(),
        )
        self._repo.save_review(review)
        self._review_saved.setText("저장됨")
        row = self._summary_table.currentRow()
        if row >= 0:
            self._summary_table.setItem(row, 13, QTableWidgetItem(review.status))

    def _selected_episodes(self) -> tuple[TradeEpisode, ...]:
        rows = sorted({index.row() for index in self._summary_table.selectionModel().selectedRows()})
        values: list[TradeEpisode] = []
        for row in rows:
            item = self._summary_table.item(row, 0)
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(value, TradeEpisode):
                values.append(value)
        return tuple(values)

    def _merge_selected_groups(self) -> None:
        result = self._trade_group_editor.merge(self._selected_episodes())
        self._status.setText(result.message)
        if result.changed:
            self.reload_history()

    def _split_selected_fills(self) -> None:
        rows = sorted({index.row() for index in self._fills_table.selectionModel().selectedRows()})
        fills: list[TradeFill] = []
        for row in rows:
            item = self._fills_table.item(row, 0)
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(value, TradeFill):
                fills.append(value)
        result = self._trade_group_editor.split(tuple(fills))
        self._status.setText(result.message)
        if result.changed:
            self.reload_history()

    def _reset_selected_groups(self) -> None:
        result = self._trade_group_editor.reset(self._selected_episodes())
        self._status.setText(result.message)
        if result.changed:
            self.reload_history()

    def _show_selected_fill(self) -> None:
        row = self._fills_table.currentRow()
        self._show_fill_at_row(row)

    def _show_fill_at_row(self, row: int) -> None:
        item = self._fills_table.item(row, 0) if row >= 0 else None
        fill = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(fill, TradeFill):
            return
        selection = trade_fill_selection(
            fill,
            visible_fills=self._visible_fills,
            active_episode=self._active_episode,
        )
        related = selection.fills
        self._selected_history_code = selection.stock_code
        self._selected_history_day = selection.selected_day
        self._selected_history_focus = selection.focus
        self._selected_history_range = selection.date_range
        self._history_chart.set_fills(related)
        rows = self._repo.load_bars(fill.stock_code, fill.filled_at)
        chart_rows = self._load_selected_chart_rows()
        self._history_chart.set_rows(chart_rows)
        self._apply_history_chart_state(self._history_interval.currentText(), self._history_chart)
        self._focus_selected_history()
        self._request_incomplete_trade_days(related)

    def _focus_selected_history(self) -> None:
        if self._selected_history_focus is not None:
            self._history_chart.focus_time(self._selected_history_focus)
            self._history_daily_chart.focus_time(self._selected_history_focus)

    def _refresh_selected_history_after_bars(self, code: str, *, analyze_any_active: bool = False) -> None:
        if self._selected_history_day is None or code != self._selected_history_code:
            return
        chart_rows = self._load_selected_chart_rows()
        self._history_chart.set_rows(chart_rows)
        self._focus_selected_history()
        if self._active_episode is not None and (
            analyze_any_active or self._active_episode.summary.stock_code == code
        ):
            self._show_trade_analysis(self._active_episode, chart_rows)

    def _load_selected_chart_rows(self) -> tuple[tuple[object, ...], ...]:
        if not self._selected_history_code or self._selected_history_range is None:
            return ()
        return self._repo.load_chart_bars_range(
            self._selected_history_code, self._selected_history_range[0], self._selected_history_range[1],
        )

    def _request_incomplete_trade_days(self, fills: tuple[TradeFill, ...]) -> None:
        bars_by_day = {
            day: self._repo.load_bars(self._selected_history_code, datetime.combine(day, time()))
            for day in {fill.filled_at.date() for fill in fills}
        }
        tasks = incomplete_trade_day_tasks(self._selected_history_code, fills, bars_by_day)
        if tasks and (self._backfill_worker is None or not self._backfill_worker.isRunning()):
            self._status.setText(f"체결 위치 누락 분봉 보완 중 · {len(tasks)}일")
            self._start_missing_backfill(tasks=tasks, automatic=True)

    def refresh(self) -> None:
        if not self._live_code:
            return
        now = datetime.now()
        try:
            self._repo.import_live_bars(self._monitor_db, self._live_code, now)
            rows = self._repo.load_bars(self._live_code, now)
            chart_rows = self._repo.load_chart_bars(self._live_code, now)
        except Exception:
            # 메인 DB가 쓰는 순간과 겹치면 다음 1초 갱신에서 다시 시도한다.
            return
        self._table.setRowCount(len(rows))
        self._chart.set_rows(chart_rows)
        current = now.replace(second=0, microsecond=0)
        for row_index, row in enumerate(rows):
            minute = datetime.fromisoformat(str(row[0]))
            values = (minute.strftime("%H:%M"), *row[1:6], f"{float(row[6]):,.2f}")
            source = str(row[7])
            state = "장 종료 확정" if source == "after_close_confirmed" else ("확인" if source == "api_confirmed" else ("진행 중·임시" if minute >= current else "보완 대기"))
            for column, value in enumerate((*values, state)):
                self._table.setItem(row_index, column, QTableWidgetItem(str(value)))
        if rows:
            self._table.scrollToBottom()

    def _confirm_on_new_minute(self) -> None:
        if not self._live_code:
            return
        now = datetime.now()
        key = now.strftime("%Y%m%d%H%M")
        if now.second >= 3 and key != self._last_confirmed_minute:
            self._last_confirmed_minute = key
            self.confirm_completed_bars()

    def confirm_completed_bars(self) -> None:
        if not self._live_code or (self._worker is not None and self._worker.isRunning()):
            return
        self._status.setText("완료 분봉 확인 중…")
        self._start_bar_confirmation(self._live_code)

    def _start_bar_confirmation(self, code: str, target_day: date | None = None, include_previous: bool = False) -> None:
        worker = ConfirmWorker(self._minute_service, code, target_day, include_previous)
        worker.completed.connect(self._confirmed)
        worker.failed.connect(lambda message: self._status.setText(f"확인 실패 · {message}"))
        worker.finished.connect(self._worker_finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker; worker.start()

    def _worker_finished(self) -> None:
        self._worker = None

    def _confirmed(self, code: str, bars: object, checked_at: object, source: str) -> None:
        if isinstance(bars, tuple) and isinstance(checked_at, datetime):
            self._repo.upsert_bars(code, bars, source, checked_at)
            self._status.setText(f"{checked_at:%H:%M:%S} 확인 · {len(bars)}개")
            if self._selected_history_day is not None and code == self._selected_history_code:
                self._refresh_selected_history_after_bars(code)
            if self._live_code == code:
                self.refresh()
            if self._detached_chart_window is not None:
                active_code = (
                    self._active_episode.summary.stock_code
                    if self._active_episode is not None
                    else ""
                )
                if code == active_code or code in self._detached_chart_window.selected_stock_codes():
                    self._sync_detached_charts()
                    if bars:
                        # 여러 비교 차트가 선택됐다면 현재 저장을 반영한 뒤 다음 종목을 이어서 보완한다.
                        QTimer.singleShot(0, self._detached_source_changed)

    def shutdown(self) -> None:
        self._refresh_timer.stop(); self._confirm_timer.stop(); self._auto_backfill_timer.stop(); self._history_sync_timer.stop(); self._central_sync_timer.stop()
        self._pending_market_index_day = None
        for attribute in ("_worker", "_history_worker", "_backfill_worker", "_daily_chart_worker", "_market_index_worker"):
            worker = getattr(self, attribute, None)
            try:
                if worker is not None and worker.isRunning():
                    worker.requestInterruption(); worker.wait(3_000)
            except RuntimeError:
                # deleteLater로 이미 정리된 QThread 래퍼는 다시 조회하지 않는다.
                pass
            setattr(self, attribute, None)

    def closeEvent(self, event: QCloseEvent) -> None:
        settings = QSettings("KiwoomMonitor", "TradingJournalWindow")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("history_splitter", self._history_splitter.saveState())
        settings.setValue("chart_interval", self._history_interval.currentText())
        settings.sync()
        # 매매일지는 실시간 구독을 유지하지 않는다. 보조 창을 먼저 닫고
        # 본창의 정상 close를 승인해 Qt가 마지막 창 종료와 함께 이벤트
        # 루프까지 끝내도록 한다. app.quit()만 호출하면 Windows에서 위젯은
        # 삭제됐지만 이벤트 루프가 남아 다음 show 명령이 죽은 위젯으로
        # 전달되는 반쪽 종료가 생길 수 있다.
        detached = self._detached_chart_window
        if detached is not None:
            try:
                detached.close()
            except RuntimeError:
                pass
        self.shutdown()
        event.accept()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.setProperty("journal_terminating", True)
            app.exit(0)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--monitor-database", required=True)
    parser.add_argument("--journal-database", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--command-file", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    options = parser.parse_args(arguments)
    crash_log = Path(options.journal_database).with_name("journal-crash.log")
    def record_uncaught(error_type: type[BaseException], error: BaseException, trace: object) -> None:
        try:
            with crash_log.open("a", encoding="utf-8") as output:
                output.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] uncaught exception\n")
                traceback.print_exception(error_type, error, trace, file=output)
        finally:
            sys.__excepthook__(error_type, error, trace)
    sys.excepthook = record_uncaught
    _set_taskbar_app_id()
    # 프로세스가 먼저 뜬 뒤 첫 show 명령을 100ms 폴링으로 받으므로, 표시된
    # 창이 잠시 없다는 이유만으로 Qt가 시작 직후 종료되면 안 된다. 실제
    # 본창 closeEvent에서는 journal_terminating 표시 후 app.exit(0)으로
    # 프로세스를 명시적으로 끝낸다.
    app = QApplication(sys.argv[:1]); app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(_application_icon_path())))
    window = JournalWindow(Path(options.monitor_database), Path(options.journal_database), Path(options.config))
    window.setWindowIcon(app.windowIcon())
    command_path = Path(options.command_file); last_id = -1
    state_path = command_path.with_name("journal_process.pid")
    state_path.write_text(
        json.dumps(process_identity_document(), ensure_ascii=True), encoding="ascii"
    )
    parent_pid = options.parent_pid
    parent_missing_since: float | None = None

    def poll() -> None:
        nonlocal last_id, parent_pid, parent_missing_since
        if bool(app.property("journal_terminating")):
            return
        try:
            command = json.loads(command_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            command = {}
        try:
            requested_parent = int(command.get("parent_pid", 0))
        except (TypeError, ValueError):
            requested_parent = 0
        if requested_parent > 0 and _parent_is_alive(requested_parent):
            parent_pid = requested_parent
            parent_missing_since = None
        if not _parent_is_alive(parent_pid):
            now = monotonic_time.monotonic()
            if parent_missing_since is None:
                parent_missing_since = now
            elif now - parent_missing_since >= 5.0:
                app.setProperty("journal_terminating", True)
                timer.stop(); window.shutdown(); app.exit(0)
            return
        parent_missing_since = None
        request_id = int(command.get("request_id", -1))
        if request_id <= last_id:
            return
        last_id = request_id
        if command.get("action") == "shutdown":
            app.setProperty("journal_terminating", True)
            timer.stop(); window.shutdown(); app.exit(0); return
        if command.get("action") == "parent":
            return
        if command.get("action") == "sync":
            window._quit_after_history_sync = True
            if not window._auto_sync_history_once():
                window._quit_after_history_sync = False
                app.setProperty("journal_terminating", True)
                timer.stop(); window.shutdown(); app.exit(0)
            return
        window.set_stock(str(command.get("code", "")), str(command.get("name", "")))
        window.showNormal(); window.raise_(); window.activateWindow()

    timer = QTimer(); timer.timeout.connect(poll); timer.start(100)
    try:
        return app.exec()
    finally:
        try:
            state_pid, state_token = read_process_identity(state_path)
            own_identity = process_identity_document()
            if state_pid == own_identity["pid"] and state_token == own_identity["start_token"]:
                state_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
