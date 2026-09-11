from __future__ import annotations

import logging
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QCloseEvent, QDesktopServices, QGuiApplication, QPainter, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from kiwoom_monitor.infrastructure.naver_news import (
    LocalNaverNewsConfig,
    NaverNewsClient,
    NaverNewsCredentials,
    NewsAISettings,
    NewsFilterSettings,
    OfficialNewsSettings,
    StockNewsItem,
    is_excluded_news,
    news_provider,
)
from kiwoom_monitor.application.news_grouping import NewsEventGroup, group_similar_news
from kiwoom_monitor.application.news_auto_analysis import (
    auto_candidate_identities,
    next_auto_groups,
    unanalyzed_groups_from,
)
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import (
    NewsAIRepository,
    StoredAINewsAnalysis,
    news_identity,
)
from kiwoom_monitor.infrastructure.news_ai import (
    DEFAULT_MODELS, AINewsAnalysis, AIRequestUsage,
)
from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
from kiwoom_monitor.infrastructure.central_ai_client import CentralAIClient
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.central_operational_settings import CentralOperationalSettingsClient
from kiwoom_monitor.presentation.news_workers import (
    AINewsWorker,
    NEWS_CHECK_INTERVAL_SECONDS,
    NewsPrepareWorker,
    NewsSearchWorker,
)
from kiwoom_monitor.presentation.news_settings_dialog import NaverNewsSettingsDialog
from kiwoom_monitor.presentation.news_execution import (
    ai_progress_text,
    ai_start_block_reason,
    automatic_ai_run_allowed,
    dispose_finished_worker,
)
from kiwoom_monitor.presentation.news_view_model import (
    ai_detail_html,
    build_display_row,
    effective_judgment,
    escape_html,
    related_articles_html,
)


logger = logging.getLogger(__name__)


class NewsCellMarkerDelegate(QStyledItemDelegate):
    """선택 행 배경을 보존하면서 실제 클릭/호버 셀 하나만 표시한다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected_cell: tuple[int, int] | None = None

    def set_selected_cell(self, cell: tuple[int, int] | None) -> None:
        self._selected_cell = cell

    def paint(self, painter: QPainter, option: object, index: object) -> None:
        # Qt의 호버 상태를 사용하면 마우스가 떠난 이전 셀과 새 셀이 함께
        # 다시 그려진다. 전역 커서 좌표를 직접 확인하면 새 셀만 갱신되어
        # 이전 셀 왼쪽 표시가 잔상으로 남을 수 있다.
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        active = self._selected_cell == (index.row(), index.column()) or hovered
        cell_option = QStyleOptionViewItem(option)
        cell_option.state &= ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_HasFocus
        )
        super().paint(painter, cell_option, index)
        if active:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#0078D7"))
            painter.drawRoundedRect(option.rect.left() + 1, option.rect.center().y() - 7, 2, 14, 1, 1)
            painter.restore()


class StockNewsWindow(QDialog):
    CHECK_INTERVAL_SECONDS = NEWS_CHECK_INTERVAL_SECONDS
    AUTO_REFRESH_MS = 180_000
    STATUS_NOTICE = (
        "기본 판단은 제목·요약 규칙이고, AI 분석은 가져올 수 있는 기사 원문을 읽습니다. "
        "모두 투자 판단을 대신하지 않으며, 원문은 기본 브라우저에서 엽니다."
    )
    STATUS_RESTORE_MS = 7000

    def __init__(self, config_path: Path, database_path: Path, parent: QWidget | None = None) -> None:
        # 부모가 있는 최상위 창은 Windows에서 '소유 창'이 되어 부모보다 항상
        # 앞에 남는다. 독립 창으로 만들고 위치·종료 연동만 직접 관리한다.
        super().__init__(None, Qt.WindowType.Window)
        self._main_window = parent
        self._allow_close = False
        self.setWindowTitle("종목 뉴스 (시험 기능)")
        self.resize(980, 650)
        self.setMinimumSize(720, 460)
        self._window_settings = QSettings("KiwoomMonitor", "StockNewsWindow")
        self._position_initialized = self._restore_window_geometry()
        self._config = LocalNaverNewsConfig(config_path)
        self._database_path = database_path
        self._repository = StockNewsRepository(database_path)
        self._ai_repository = NewsAIRepository(database_path)
        self._central_news_client: CentralNewsClient | None = None
        self._central_ai_client: CentralAIClient | None = None
        self._central_operational_client: CentralOperationalSettingsClient | None = None
        try:
            source = DataSourceConfig(config_path.with_name("data_source.json")).load()
            if source.mode in {"local_server", "personal_server"}:
                self._central_news_client = CentralNewsClient(source.server_url, source.access_token)
                self._central_ai_client = CentralAIClient(source.server_url, source.access_token)
                self._central_operational_client = CentralOperationalSettingsClient(source)
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("중앙 뉴스 설정을 읽지 못해 로컬 뉴스 모드를 사용합니다.", exc_info=True)
        self._news_filter = NewsFilterSettings()
        try:
            self._news_filter = self._config.load_filter()
        except (OSError, ValueError):
            logger.warning("저장된 뉴스 필터 설정을 읽지 못해 기본값을 사용합니다.", exc_info=True)
        self._stock_code = ""
        self._stock_name = ""
        self._journal_group_id = ""
        self._journal_trade_date = ""
        self._items: tuple[StockNewsItem, ...] = ()
        self._visible_items: tuple[StockNewsItem, ...] = ()
        self._visible_groups: tuple[NewsEventGroup, ...] = ()
        self._selected_news_cell: tuple[int, int] | None = None
        self._worker: NewsSearchWorker | None = None
        self._prepare_worker: NewsPrepareWorker | None = None
        self._prepare_request_id = 0
        self._pending_prepare: tuple[int, str] | None = None
        self._pending_new_identities: set[str] = set()
        self._ai_result_cache: dict[str, StoredAINewsAnalysis] = {}
        self._render_generation = 0
        self._render_row = 0
        self._settings_dialog: NaverNewsSettingsDialog | None = None
        self._ai_worker: AINewsWorker | None = None
        self._ai_item: StockNewsItem | None = None
        self._ai_groups: tuple[NewsEventGroup, ...] = ()
        self._ai_stock_code = ""
        self._ai_continue = False
        self._ai_automatic_run = False
        self._manual_ai_queue = False
        self._auto_ai_identities: set[str] = set()
        self._pending_refresh = False
        self._auto_refresh = QTimer(self)
        self._auto_refresh.setInterval(self.AUTO_REFRESH_MS)
        self._auto_refresh.timeout.connect(self._schedule_prepare)

        self._stock_label = QLabel("메인 표에서 종목명을 클릭하세요.")
        self._stock_label.setStyleSheet("font-size: 17px; font-weight: 700;")
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #475467;")
        self._status_label = QLabel(self.STATUS_NOTICE)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #667085; padding: 4px;")
        self._status_restore_timer = QTimer(self)
        self._status_restore_timer.setSingleShot(True)
        self._status_restore_timer.setInterval(self.STATUS_RESTORE_MS)
        self._status_restore_timer.timeout.connect(self._restore_status_notice)
        self._auto_ai_toggle = QCheckBox("AI 자동 분석")
        try:
            self._auto_ai_toggle.setChecked(self._config.load_ai().auto_analyze)
        except (OSError, ValueError):
            pass
        self._auto_ai_toggle.toggled.connect(self._toggle_auto_analysis)
        self._show_low_relevance = QCheckBox("관련성 낮은 뉴스도 보기")
        self._show_low_relevance.toggled.connect(self._schedule_prepare)
        refresh = QPushButton("새로고침")
        refresh.clicked.connect(lambda: self.refresh(force=True))
        settings = QPushButton("⚙")
        settings.setToolTip("뉴스 설정")
        settings.setAccessibleName("뉴스 설정")
        settings.setFixedWidth(36)
        settings.clicked.connect(self._open_settings)
        self._window_mode = QComboBox()
        self._window_mode.addItem("독립 창", "independent")
        if self._main_window is not None:
            self._window_mode.addItem("메인창에 연결", "attached")
            self._window_mode.setToolTip(
                "독립 창: 메인창과 뉴스창 중 클릭한 창이 앞으로 옵니다.\n"
                "메인창에 연결: 뉴스창이 메인창에 소속되어 메인창보다 앞에 유지됩니다."
            )
        else:
            self._window_mode.addItem("메인창과 함께 앞으로", "linked")
            self._window_mode.addItem("메인창 오른쪽에 고정", "docked_right")
            self._window_mode.addItem("메인창 왼쪽에 고정", "docked_left")
            self._window_mode.addItem("메인창 위쪽에 고정", "docked_top")
            self._window_mode.addItem("메인창 아래쪽에 고정", "docked_bottom")
            self._window_mode.setToolTip(
                "독립 창: 두 창을 따로 전환합니다.\n"
                "메인창과 함께 앞으로: 메인창을 선택하면 뉴스창도 함께 보이게 올립니다.\n"
                "고정: 함께 올리고 선택한 상·하·좌·우 위치를 유지합니다."
            )
        saved_window_mode = str(self._window_settings.value("window_mode", "independent"))
        if saved_window_mode == "docked":
            saved_window_mode = "docked_right"
        saved_window_mode_index = self._window_mode.findData(saved_window_mode)
        self._window_mode.setCurrentIndex(max(0, saved_window_mode_index))
        self._window_mode.currentIndexChanged.connect(self._change_window_mode)
        top = QHBoxLayout()
        top.addWidget(self._stock_label)
        top.addStretch()
        top.addWidget(self._auto_ai_toggle)
        top.addWidget(self._show_low_relevance)
        top.addWidget(refresh)
        top.addWidget(self._window_mode)
        top.addWidget(settings)

        self._shortcut_layout = QHBoxLayout()
        self._shortcut_layout.setContentsMargins(0, 0, 0, 0)
        self._shortcut_layout.addWidget(self._count_label)
        self._rebuild_shortcuts()

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(("시각", "제공처", "분류", "판단", "제목"))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Qt의 행 선택 테두리는 모든 셀 왼쪽에 선택 표시를 그릴 수 있다.
        # 선택 행 배경과 클릭 셀 표시는 아래에서 직접 관리한다.
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setMouseTracking(True)
        self._table.viewport().setMouseTracking(True)
        marker_delegate = NewsCellMarkerDelegate(self._table)
        self._table.setItemDelegate(marker_delegate)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._apply_column_visibility()
        self._table.cellClicked.connect(self._select_news_cell)
        self._table.cellDoubleClicked.connect(self._on_news_cell_double_clicked)

        self._detail = QTextBrowser()
        self._detail.setOpenExternalLinks(True)
        self._detail.setPlaceholderText("뉴스를 선택하면 네이버가 제공한 요약과 분류 이유가 표시됩니다.")
        self._open_button = QPushButton("원문 보기")
        self._open_button.setEnabled(False)
        self._open_button.clicked.connect(self._open_selected)
        self._ai_button = QPushButton("AI 원문 분석")
        self._ai_button.setEnabled(False)
        self._ai_button.clicked.connect(lambda _checked=False: self._analyze_selected(automatic=False))
        self._continue_ai_button = QPushButton("선택 위치부터 미분석 이어서 분석")
        self._continue_ai_button.setToolTip("선택한 행부터 과거 방향으로 미분석 사건을 설정 건수만큼 분석합니다.")
        self._continue_ai_button.clicked.connect(self._analyze_unanalyzed_from_selection)
        self._journal_link_button = QPushButton("매매일지에 추가")
        self._journal_link_button.setEnabled(False)
        self._journal_link_button.clicked.connect(self._toggle_journal_link)
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self._detail, 1)
        detail_buttons = QHBoxLayout(); detail_buttons.addWidget(self._ai_button); detail_buttons.addWidget(self._continue_ai_button); detail_buttons.addWidget(self._journal_link_button); detail_buttons.addStretch(); detail_buttons.addWidget(self._open_button)
        detail_layout.addLayout(detail_buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(detail_panel)
        splitter.setSizes((390, 210))

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(self._shortcut_layout)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._status_label)
        self._apply_window_mode(str(self._window_mode.currentData()), persist=False)

    def set_stock(self, code: str, name: str, *, activate: bool = True,
                  journal_group_id: str = "", trade_date: str = "") -> None:
        changed = code != self._stock_code
        context_changed = journal_group_id.strip() != self._journal_group_id or trade_date.strip() != self._journal_trade_date
        if changed:
            self._pending_new_identities.clear()
            self._auto_ai_identities.clear()
        self._stock_code = code
        self._stock_name = name.strip()
        self._journal_group_id = journal_group_id.strip()
        self._journal_trade_date = trade_date.strip()
        date_suffix = f" · 매매일 {self._journal_trade_date}" if self._journal_trade_date else ""
        self._stock_label.setText(f"{self._stock_name} ({self._stock_code}){date_suffix}")
        if changed or context_changed:
            self._schedule_prepare()
            self._status_label.setText(f"{self._stock_name}의 저장된 뉴스를 준비하는 중…")
        if not self._position_initialized:
            self._position_beside_main_window()
            self._position_initialized = True
        if not self.isVisible():
            self.show()
        if activate:
            self.raise_()
            self.activateWindow()

    def _schedule_prepare(self) -> None:
        if not self._stock_code:
            return
        self._prepare_request_id += 1
        self._pending_prepare = (self._prepare_request_id, self._stock_code)
        if self._prepare_worker is None:
            self._start_pending_prepare()

    def _start_pending_prepare(self) -> None:
        pending = self._pending_prepare
        if pending is None or self._prepare_worker is not None:
            return
        self._pending_prepare = None
        request_id, stock_code = pending
        worker = NewsPrepareWorker(
            request_id, stock_code, self._stock_name, self._database_path, self._news_filter,
            self._show_low_relevance.isChecked(), self,
        )
        self._prepare_worker = worker
        worker.completed.connect(self._on_prepare_completed)
        worker.failed.connect(self._on_prepare_failed)
        worker.finished.connect(self._on_prepare_finished)
        worker.start()

    def _on_prepare_completed(self, request_id: int, stock_code: str, items: object,
                              groups: object, ai_results: object, recently_checked: bool,
                              last_naver_check: object) -> None:
        if request_id != self._prepare_request_id or stock_code != self._stock_code:
            return
        if not isinstance(items, tuple) or not isinstance(groups, tuple) or not isinstance(ai_results, dict):
            return
        self._items = items
        if self._journal_trade_date:
            try:
                target_day = datetime.fromisoformat(self._journal_trade_date).date()
                groups = tuple(
                    group for group in groups
                    if any(item.published_at and item.published_at.astimezone().date() == target_day for item in group.items)
                )
            except ValueError:
                pass
        self._visible_groups = groups
        self._visible_items = tuple(group.representative for group in groups)
        self._ai_result_cache = ai_results
        self._render_items()
        relevant_count = sum(item.assessment.relevant for item in items)
        self._count_label.setText(f"저장된 뉴스 {len(items)}건 · 증권 관련 {relevant_count}건")
        # 진행 결과를 잠시 보여준 뒤 맨 아래 기본 안내로 돌아간다.
        self._schedule_status_notice()
        if not recently_checked:
            self._start_news_search(
                last_naver_check if isinstance(last_naver_check, datetime) else None,
            )
        if self._pending_new_identities:
            self._configure_auto_candidates(self._pending_new_identities)
            self._pending_new_identities.clear()
        else:
            # 프로세스 재시작이나 종목 전환 경쟁으로 메모리 후보가 사라져도
            # 현재 종목의 '최신 N건' 범위 안에서 미분석 대표 기사를 복구한다.
            self._configure_recent_auto_candidates()
        self._resume_auto_analysis()

    def _on_prepare_failed(self, request_id: int, stock_code: str, message: str) -> None:
        if request_id == self._prepare_request_id and stock_code == self._stock_code:
            self._status_label.setText(f"뉴스 준비 실패: {message}")
            self._schedule_status_notice()

    def _on_prepare_finished(self) -> None:
        worker = self._prepare_worker
        self._prepare_worker = None
        dispose_finished_worker(worker)
        self._start_pending_prepare()

    def refresh(self, *, force: bool = False) -> None:
        if not self._stock_name:
            return
        if not force and self._repository.recently_checked(self._stock_code, self.CHECK_INTERVAL_SECONDS):
            if self._items:
                self._status_label.setText(f"저장된 최신 뉴스 {len(self._items)}건")
            return
        if self._worker is not None and self._worker.isRunning():
            self._pending_refresh = True
            self._status_label.setText(f"{self._stock_name} 뉴스 조회 대기 중…")
            return
        self._start_news_search(self._repository.last_naver_checked_at(self._stock_code))

    def _start_news_search(self, last_naver_check: datetime | None) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._pending_refresh = True
            self._status_label.setText(f"{self._stock_name} 뉴스 조회 대기 중…")
            return
        try:
            credentials = self._config.load()
        except (OSError, ValueError):
            credentials = NaverNewsCredentials()
        try:
            official = self._config.load_official()
        except (OSError, ValueError):
            official = OfficialNewsSettings()
        try:
            ai_settings = self._config.load_ai()
        except (OSError, ValueError):
            ai_settings = NewsAISettings()
        if self._central_news_client is None and (not credentials.client_id or not credentials.client_secret) and not (official.dart_enabled and official.dart_api_key):
            self._status_label.setText("뉴스 API 설정이 필요합니다. 저장된 뉴스는 그대로 표시합니다.")
            return
        requested_code = self._stock_code
        requested_name = self._stock_name
        two_days_ago = datetime.now(UTC) - timedelta(days=2)
        naver_since = max(two_days_ago, last_naver_check.astimezone(UTC)) if last_naver_check else two_days_ago
        worker = NewsSearchWorker(requested_code, requested_name, credentials, official,
                                  self._config.directory / "dart_corp_codes.json", naver_since,
                                  self._database_path, self._central_news_client, ai_settings, self)
        self._worker = worker
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._status_label.setText(f"{requested_name}의 증권 관련 뉴스를 찾는 중…")
        worker.start()

    def _on_completed(self, stock_code: str, stock_name: str, items: object,
                      naver_succeeded: bool, new_identities: object, new_count: int) -> None:
        if not isinstance(items, tuple) or not isinstance(new_identities, set):
            return
        if stock_code == self._stock_code:
            update_text = f"새 뉴스 {new_count}건 저장" if new_count else "새 뉴스 없음 · 저장된 내용 유지"
            self._status_label.setText(f"{update_text} · 뉴스 목록을 백그라운드에서 정리하는 중…")
            self._pending_new_identities = new_identities
            self._schedule_prepare()

    def _configure_auto_candidates(self, new_identities: set[str]) -> None:
        try:
            ai = self._config.load_ai()
        except (OSError, ValueError):
            ai = NewsAISettings()
        if not ai.auto_analyze:
            return
        self._auto_ai_identities = set(auto_candidate_identities(
            self._visible_groups,
            self._current_ai_result_identities(),
            ai.auto_recent_limit,
            new_identities=new_identities,
        ))

    def _current_ai_result_identities(self) -> set[str]:
        """표에는 레거시 결과를 보이되 자동분석 완료로는 세지 않는다."""
        return {
            identity for identity, stored in self._ai_result_cache.items()
            if getattr(stored, "uses_current_prompt", True)
        }

    def _configure_recent_auto_candidates(self) -> None:
        try:
            ai = self._config.load_ai()
        except (OSError, ValueError):
            return
        if not ai.auto_analyze:
            return
        # 앱을 다시 열었을 때는 과거 프롬프트 버전 결과도 이미 분석된 기사로
        # 센다. 규칙 변경만으로 최근 100건을 자동 재요청하지 않는다.
        self._auto_ai_identities = set(auto_candidate_identities(
            self._visible_groups,
            self._ai_result_cache.keys(),
            ai.auto_recent_limit,
        ))

    def _on_failed(self, stock_code: str, stock_name: str, message: str) -> None:
        if stock_code == self._stock_code:
            suffix = " · 저장된 뉴스를 표시합니다." if self._items else ""
            self._status_label.setText(message + suffix)
            self._schedule_status_notice()

    def _on_finished(self) -> None:
        worker = self._worker
        self._worker = None
        dispose_finished_worker(worker)
        if self._pending_refresh:
            self._pending_refresh = False
            self._schedule_prepare()
            return
        # completed 신호는 QThread가 완전히 끝나기 직전에 전달된다. finished까지
        # 기다려 네이버의 모든 페이지와 DART 조회가 종료된 뒤 AI를 시작한다.
        self._resume_auto_analysis()

    def _render_items(self) -> None:
        # ResizeToContents 상태에서 셀을 하나씩 넣으면 셀마다 열 너비를 다시
        # 계산해 메인 UI 이벤트까지 잠깐씩 밀린다. 채우는 동안은 현재 너비를
        # 고정하고, 소량의 행만 넣은 뒤 이벤트 루프에 제어를 돌려준다.
        self._render_generation += 1
        generation = self._render_generation
        header = self._table.horizontalHeader()
        for column in range(self._table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self._table.setRowCount(len(self._visible_items))
        self._render_row = 0
        self._selected_news_cell = None
        delegate = self._table.itemDelegate()
        if isinstance(delegate, NewsCellMarkerDelegate):
            delegate.set_selected_cell(None)
        self._detail.clear()
        self._open_button.setEnabled(False)
        self._ai_button.setEnabled(False)
        QTimer.singleShot(0, lambda: self._render_item_chunk(generation))

    def _render_item_chunk(self, generation: int) -> None:
        if generation != self._render_generation:
            return
        end = min(self._render_row + 12, len(self._visible_items))
        for row in range(self._render_row, end):
            item = self._visible_items[row]
            group = self._visible_groups[row]
            stored = self._ai_result_cache.get(news_identity(item))
            display = build_display_row(item, group, stored)
            values = (display.published, display.provider, display.category, display.outlook, display.title)
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 3:
                    cell.setForeground(_outlook_color(self._effective_judgment(item)[1], self._news_filter))
                    font = cell.font(); font.setBold(True); cell.setFont(font)
                self._table.setItem(row, column, cell)
        self._render_row = end
        if end < len(self._visible_items):
            QTimer.singleShot(0, lambda: self._render_item_chunk(generation))
            return
        self._apply_column_visibility()

    def _apply_column_visibility(self) -> None:
        if not hasattr(self, "_table"):
            return
        keys = ("time", "provider", "category", "outlook", "title")
        visible = set(self._news_filter.visible_columns)
        if not visible:
            visible = set(keys)
        for column, key in enumerate(keys):
            self._table.setColumnHidden(column, key not in visible)
        last_visible = max((column for column, key in enumerate(keys) if key in visible), default=4)
        header = self._table.horizontalHeader()
        for column in range(len(keys)):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.Stretch if column == last_visible else QHeaderView.ResizeMode.Interactive
            )
        default_widths = (105, 100, 125, 145, 360)
        for column, width in enumerate(default_widths):
            if column != last_visible and header.sectionSize(column) < 40:
                header.resizeSection(column, width)

    def _select_news_cell(self, row: int, column: int) -> None:
        self._selected_news_cell = (row, column)
        self._table.setCurrentCell(row, column)
        for item_row in range(self._table.rowCount()):
            background = QColor("#DDEBF7") if item_row == row else QBrush()
            for item_column in range(self._table.columnCount()):
                cell = self._table.item(item_row, item_column)
                if cell is not None:
                    cell.setBackground(background)
        delegate = self._table.itemDelegate()
        if isinstance(delegate, NewsCellMarkerDelegate):
            delegate.set_selected_cell(self._selected_news_cell)
        self._table.viewport().update()
        self._show_detail(row)

    def _on_news_cell_double_clicked(self, row: int, column: int) -> None:
        """판단 더블클릭은 AI 분석, 제목 더블클릭은 원문 열기로 동작한다."""
        self._select_news_cell(row, column)
        if column == 3:
            self._analyze_selected(automatic=False)
        elif column == 4:
            self._open_item(row)

    def _show_detail(self, row: int) -> None:
        if row < 0 or row >= len(self._visible_items):
            return
        item = self._visible_items[row]
        group = self._visible_groups[row]
        assessment = item.assessment
        category, outlook, reason, judgment_source = self._effective_judgment(item)
        self._detail.setHtml(
            f"<h3>{_html(item.title)}</h3>"
            + self._ai_html(item)
            + f"<p><b>최종 판단: {_html(outlook)}</b> · {_html(category)} · 제공처: {_html(news_provider(item))}</p>"
            f"<p>{_html(item.description) or '제공된 요약이 없습니다.'}</p>"
            f"<hr><p><b>최종 판단 이유:</b> {_html(reason)}</p>"
            f"<p style='color:#667085'>판단 기준: {_html(judgment_source)} · 관련성 점수 {assessment.relevance_score}</p>"
            + self._related_articles_html(group)
        )
        self._open_button.setEnabled(bool(item.link or item.original_link))
        linked = news_identity(item) in self._repository.journal_linked_identities(self._journal_group_id, self._stock_code) if self._journal_group_id else False
        self._journal_link_button.setEnabled(bool(self._journal_group_id))
        self._journal_link_button.setText("매매일지에서 제거" if linked else "매매일지에 추가")
        try:
            ai = self._config.load_ai()
        except (OSError, ValueError):
            ai = NewsAISettings()
        self._ai_button.setEnabled(bool((item.link or item.original_link) and ai.provider != "none" and ai.api_key)
                                   and (self._ai_worker is None or not self._ai_worker.isRunning()))

    def _toggle_journal_link(self) -> None:
        row = self._table.currentRow()
        if not self._journal_group_id or row < 0 or row >= len(self._visible_items):
            return
        item = self._visible_items[row]; identity = news_identity(item)
        linked = identity in self._repository.journal_linked_identities(self._journal_group_id, self._stock_code)
        self._repository.set_journal_link(self._journal_group_id, self._stock_code, identity, not linked)
        self._show_detail(row)
        self._status_label.setText("매매일지 연결을 해제했습니다." if linked else "대표 뉴스를 매매일지에 보존했습니다.")

    @staticmethod
    def _related_articles_html(group: NewsEventGroup) -> str:
        return related_articles_html(group)

    def _effective_judgment(self, item: StockNewsItem) -> tuple[str, str, str, str]:
        return effective_judgment(item, self._ai_result_cache.get(news_identity(item)))

    def _ai_html(self, item: StockNewsItem) -> str:
        return ai_detail_html(item, self._ai_result_cache.get(news_identity(item)))

    def _analyze_selected(self, *, automatic: bool = False) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._visible_items) or self._ai_worker is not None:
            return
        self._start_ai_analysis(self._visible_groups[row], automatic=automatic)

    def _start_ai_analysis(self, group: NewsEventGroup, *, automatic: bool) -> None:
        self._start_ai_groups((group,), automatic=automatic)

    def _start_ai_groups(self, groups: tuple[NewsEventGroup, ...], *, automatic: bool) -> None:
        if self._ai_worker is not None:
            return
        try:
            settings = self._config.load_ai()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "AI 분석", str(error)); return
        used = self._ai_repository.daily_count()
        block_reason = ai_start_block_reason(settings, used, len(groups))
        if block_reason == "daily_limit":
            QMessageBox.information(self, "AI 분석", f"오늘 설정한 상한 {settings.daily_limit}건을 모두 사용했습니다."); return
        if block_reason == "empty":
            return
        self._ai_groups = groups
        self._ai_item = groups[0].representative
        self._ai_stock_code = self._stock_code
        self._ai_continue = False
        self._ai_automatic_run = automatic
        self._ai_worker = AINewsWorker(
            groups, self._stock_name, settings, self,
            stock_code=self._stock_code, central_client=self._central_ai_client,
        )
        self._ai_worker.completed.connect(self._on_ai_completed)
        self._ai_worker.failed.connect(self._on_ai_failed)
        self._ai_worker.finished.connect(self._on_ai_finished)
        self._ai_button.setEnabled(False)
        self._ai_button.setText("AI 분석 중…")
        self._status_restore_timer.stop()
        progress = ai_progress_text(settings, used)
        related_count = sum(len(group.items) for group in groups)
        self._status_label.setText(
            f"AI 요청 1회로 사건 {len(groups)}개·관련 기사 {related_count}건을 읽고 있습니다… ({progress})"
        )
        self._ai_worker.start()

    def _analyze_unanalyzed_from_selection(self) -> None:
        if self._ai_worker is not None:
            return
        try:
            settings = self._config.load_ai()
        except (OSError, ValueError) as error:
            self._status_label.setText(f"AI 설정을 읽지 못했습니다: {error}")
            return
        start = max(0, self._table.currentRow())
        candidates = unanalyzed_groups_from(
            self._visible_groups,
            start,
            self._current_ai_result_identities(),
            settings.auto_recent_limit,
        )
        if not candidates:
            self._status_label.setText("선택 위치 이후에 미분석 뉴스가 없습니다.")
            return
        self._manual_ai_queue = True
        if settings.request_mode == "single":
            self._auto_ai_identities.update(news_identity(group.representative) for group in candidates[1:])
            self._start_ai_groups((candidates[0],), automatic=True)
        else:
            self._auto_ai_identities.update(news_identity(group.representative) for group in candidates[settings.batch_size:])
            self._start_ai_groups(candidates[:settings.batch_size], automatic=True)

    def _auto_analyze_next(self) -> None:
        if (self._worker is not None and self._worker.isRunning()) \
                or self._ai_worker is not None or not self._auto_ai_identities:
            return
        try:
            settings = self._config.load_ai()
        except (OSError, ValueError):
            return
        if not automatic_ai_run_allowed(
            settings,
            manual_queue=self._manual_ai_queue,
            central_client_available=self._central_ai_client is not None,
            used_requests=self._ai_repository.daily_count(),
        ):
            return
        candidates = next_auto_groups(
            self._visible_items,
            self._visible_groups,
            self._auto_ai_identities,
            self._current_ai_result_identities(),
            settings.request_mode,
            settings.batch_size,
        )
        if candidates:
            for group in candidates:
                self._auto_ai_identities.discard(news_identity(group.representative))
            self._start_ai_groups(candidates, automatic=True)
            return
        self._auto_ai_identities.clear()
        self._manual_ai_queue = False

    def _resume_auto_analysis(self) -> None:
        """후보 생성·뉴스 조회·이전 AI 종료 순서와 무관하게 대기열을 재확인한다."""
        if self._auto_ai_identities:
            QTimer.singleShot(0, self._auto_analyze_next)

    def _on_ai_completed(self, results: object, body_hashes: object, provider: str, model: str,
                         usage: object, article_count: int) -> None:
        if not isinstance(results, tuple) or not isinstance(body_hashes, tuple) or len(results) != len(self._ai_groups):
            return
        request_usage = usage if isinstance(usage, AIRequestUsage) else AIRequestUsage()
        request_mode = "batch" if len(self._ai_groups) > 1 else "single"
        central_cache_hit = bool(getattr(self._ai_worker, "central_cache_hit", False))
        if not central_cache_hit:
            self._ai_repository.log_request(provider, model, request_mode, len(self._ai_groups), article_count, request_usage)
        for group, body_hash, result in zip(self._ai_groups, body_hashes, results, strict=True):
            if not isinstance(result, AINewsAnalysis):
                continue
            item = group.representative
            self._ai_repository.save(self._ai_stock_code, item, provider, model, str(body_hash), result)
            if self._ai_stock_code == self._stock_code:
                self._ai_result_cache[news_identity(item)] = StoredAINewsAnalysis(
                    result, provider, model, datetime.now(UTC), str(body_hash),
                )
        requests, input_tokens, output_tokens, total_tokens = self._ai_repository.daily_usage()
        self._status_label.setText(
            f"AI 요청 1회로 사건 {len(self._ai_groups)}개 저장 · 오늘 요청 {requests}회 · "
            f"이번 입력 {request_usage.input_tokens:,} TPM · 오늘 토큰 입력 {input_tokens:,}/출력 {output_tokens:,}/합계 {total_tokens:,}"
        )
        self._ai_continue = self._ai_automatic_run and bool(self._auto_ai_identities)
        if self._ai_stock_code == self._stock_code:
            for group in self._ai_groups:
                analyzed_identity = news_identity(group.representative)
                row = next((index for index, item in enumerate(self._visible_items)
                            if news_identity(item) == analyzed_identity), -1)
                if not (0 <= row < len(self._visible_items)):
                    continue
                category, outlook, _reason, source = self._effective_judgment(self._visible_items[row])
                category_cell = self._table.item(row, 2)
                if category_cell is not None:
                    category_cell.setText(category)
                cell = self._table.item(row, 3)
                if cell is not None:
                    cell.setText(f"{outlook}  ᴬᴵ" if source.startswith("AI 원문 분석") else outlook)
                    cell.setForeground(_outlook_color(outlook, self._news_filter))
                if row == self._table.currentRow():
                    self._show_detail(row)
                    self._detail.verticalScrollBar().setValue(0)

    def _on_ai_failed(self, message: str, api_attempted: bool = False, article_count: int = 0) -> None:
        if api_attempted:
            try:
                settings = self._config.load_ai()
                model = settings.model.strip() or DEFAULT_MODELS.get(settings.provider, "")
                self._ai_repository.log_request(
                    settings.provider, model, "batch" if len(self._ai_groups) > 1 else "single",
                    len(self._ai_groups), article_count, AIRequestUsage(),
                )
            except (OSError, ValueError, sqlite3.Error):
                logger.warning("실패한 AI 요청 통계를 저장하지 못했습니다.", exc_info=True)
        self._ai_continue = self._ai_automatic_run and bool(self._auto_ai_identities)
        if self._ai_continue:
            self._status_label.setText(f"AI 분석 실패 · 이 기사를 건너뛰고 다음 기사를 계속합니다: {message}")
            logger.warning("자동 AI 원문 분석 실패, 다음 기사 계속: %s", message)
        else:
            self._status_label.setText(f"AI 분석 실패: {message}")
            # 모달 오류창은 사용자가 닫을 때까지 메인 순위표의 화면 갱신을
            # 보류시킨다. 실패 내용은 뉴스창 상태줄에 남기고 메인 수신·표시는
            # 계속 움직이게 한다.
            self._status_label.setToolTip(message)
            logger.warning("AI 원문 분석 실패: %s", message)

    def _on_ai_finished(self) -> None:
        worker = self._ai_worker
        self._ai_worker = None
        dispose_finished_worker(worker)
        self._ai_button.setText("AI 원문 분석")
        self._ai_button.setEnabled(self._table.currentRow() >= 0)
        self._ai_automatic_run = False
        self._ai_groups = ()
        # 이전 작업이 끝나기 직전 또는 끝난 직후 마지막 종목의 후보가
        # 만들어지는 두 경우 모두 여기서 다시 확인한다.
        self._resume_auto_analysis()
        self._schedule_status_notice()

    def _schedule_status_notice(self) -> None:
        self._status_restore_timer.start()

    def _restore_status_notice(self) -> None:
        news_running = self._worker is not None and self._worker.isRunning()
        prepare_running = self._prepare_worker is not None and self._prepare_worker.isRunning()
        if news_running or prepare_running or self._ai_worker is not None or self._auto_ai_identities:
            self._status_restore_timer.start()
            return
        self._status_label.setText(self.STATUS_NOTICE)
        self._status_label.setToolTip("")

    def _open_selected(self) -> None:
        self._open_item(self._table.currentRow())

    def _open_item(self, row: int) -> None:
        if row < 0 or row >= len(self._visible_items):
            return
        item = self._visible_items[row]
        url = item.link or item.original_link
        if url and not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "뉴스 원문", "기본 브라우저에서 기사를 열지 못했습니다.")

    def _open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        try:
            dialog = NaverNewsSettingsDialog(
                self._config, self, database_path=self._database_path,
                operational_client=self._central_operational_client,
            )
        except (OSError, ValueError):
            QMessageBox.warning(self, "뉴스 API 설정", "저장된 뉴스 API 설정을 읽지 못했습니다. 설정 파일을 다시 만들어 주세요.")
            return
        self._settings_dialog = dialog
        dialog.setModal(False)
        dialog.accepted.connect(self._on_settings_saved)
        dialog.finished.connect(lambda _result: self._clear_settings_dialog(dialog))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_settings_saved(self) -> None:
        self._news_filter = self._config.load_filter()
        ai = self._config.load_ai()
        self._auto_ai_toggle.blockSignals(True)
        self._auto_ai_toggle.setChecked(ai.auto_analyze)
        self._auto_ai_toggle.blockSignals(False)
        self._rebuild_shortcuts()
        self._schedule_prepare()
        self.refresh(force=True)

    def _toggle_auto_analysis(self, enabled: bool) -> None:
        try:
            ai = self._config.load_ai()
            self._config.save(
                self._config.load(), self._news_filter,
                replace(ai, auto_analyze=enabled), self._config.load_official(),
            )
        except (OSError, ValueError) as error:
            self._status_label.setText(f"AI 자동 분석 설정 저장 실패: {error}")
            return
        if enabled:
            self._configure_recent_auto_candidates()
            self._resume_auto_analysis()
            self._status_label.setText("AI 자동 분석을 켰습니다.")
        else:
            self._auto_ai_identities.clear()
            self._manual_ai_queue = False
            self._status_label.setText("AI 자동 분석을 껐습니다.")

    def _clear_settings_dialog(self, dialog: NaverNewsSettingsDialog) -> None:
        if self._settings_dialog is dialog:
            self._settings_dialog = None
        dialog.deleteLater()

    def _rebuild_shortcuts(self) -> None:
        while self._shortcut_layout.count() > 1:
            item = self._shortcut_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        try:
            shortcuts = self._config.load_shortcuts()
        except (OSError, ValueError):
            shortcuts = ()
        self._shortcut_layout.addStretch()
        for name, url in shortcuts:
            button = QPushButton(name)
            button.setToolTip(url)
            button.clicked.connect(lambda _checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))
            self._shortcut_layout.addWidget(button)

    def _change_window_mode(self) -> None:
        self._apply_window_mode(str(self._window_mode.currentData()))

    def _apply_window_mode(self, mode: str, *, persist: bool = True) -> None:
        """같은 프로세스의 소유 창 또는 분리 프로세스 표시 방식을 저장한다."""
        if self._main_window is None:
            valid_modes = {"independent", "linked", "docked_right", "docked_left", "docked_top", "docked_bottom"}
            mode = "docked_right" if mode == "docked" else mode
            mode = mode if mode in valid_modes else "independent"
        else:
            mode = "attached" if mode == "attached" else "independent"
        geometry = self.saveGeometry()
        was_visible = self.isVisible()
        if mode == "attached":
            self.setParent(self._main_window, Qt.WindowType.Window)
        else:
            self.setParent(None, Qt.WindowType.Window)
        self.setWindowTitle("종목 뉴스 (시험 기능)")
        self.restoreGeometry(geometry)
        if persist:
            self._window_settings.setValue("window_mode", mode)
            self._window_settings.sync()
        if was_visible:
            self.show()
            self.raise_()
            self.activateWindow()

    def showEvent(self, event: QShowEvent) -> None:
        self._auto_refresh.start()
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_window_geometry()
        # 메인 앱이 살아 있는 동안에는 창만 숨긴다. 네트워크 요청 중 QThread가
        # 파괴되는 문제 없이 종목명을 다시 누르면 같은 창을 즉시 재사용한다.
        if not self._allow_close and self._main_window is not None and self._main_window.isVisible():
            self._auto_refresh.stop()
            self.hide()
            event.ignore()
            return
        super().closeEvent(event)

    def shutdown(self) -> None:
        """메인 앱 종료 시 독립 뉴스창도 함께 닫는다."""
        self._save_window_geometry()
        self._allow_close = True
        self._auto_refresh.stop()
        self._prepare_request_id += 1
        self._pending_prepare = None
        worker = self._prepare_worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)
        for active_worker in (self._worker, self._ai_worker):
            if active_worker is not None and active_worker.isRunning():
                active_worker.requestInterruption()
                active_worker.wait(10_000)
        self.close()

    def _save_window_geometry(self) -> None:
        """앱 종료 직전에도 마지막 뉴스창 위치와 크기를 확실히 기록한다."""
        self._window_settings.setValue("geometry", self.saveGeometry())
        self._window_settings.sync()

    def _restore_window_geometry(self) -> bool:
        geometry = self._window_settings.value("geometry")
        if geometry is None or not self.restoreGeometry(geometry):
            return False
        window_rect = self.frameGeometry()
        if any(window_rect.intersects(screen.availableGeometry()) for screen in QGuiApplication.screens()):
            return True
        return False

    def _position_beside_main_window(self) -> None:
        parent = self._main_window
        if parent is None:
            return
        parent_rect = parent.frameGeometry()
        parent_screen = parent.screen()
        available = parent_screen.availableGeometry() if parent_screen is not None else QGuiApplication.primaryScreen().availableGeometry()
        gap = 8
        y = max(available.top(), min(parent_rect.top(), available.bottom() - self.height() + 1))
        right_x = parent_rect.right() + gap
        left_x = parent_rect.left() - self.width() - gap
        if right_x + self.width() <= available.right() + 1:
            self.move(right_x, y)
            return
        if left_x >= available.left():
            self.move(left_x, y)
            return
        for screen in QGuiApplication.screens():
            if screen is parent_screen:
                continue
            other = screen.availableGeometry()
            self.move(other.left(), other.top())
            return
        # 한 화면에 두 창이 나란히 들어가지 않으면 더 넓은 쪽 가장자리에 붙인다.
        right_space = available.right() - parent_rect.right()
        left_space = parent_rect.left() - available.left()
        x = available.right() - self.width() + 1 if right_space >= left_space else available.left()
        self.move(x, y)


def _outlook_color(outlook: str, settings: NewsFilterSettings) -> QColor:
    if "혼재" in outlook:
        return QColor(settings.mixed_color)
    if outlook.startswith("호재"):
        return QColor(settings.positive_color)
    if outlook.startswith("악재"):
        return QColor(settings.negative_color)
    return QColor(settings.neutral_color)


def _html(value: str) -> str:
    return escape_html(value)
