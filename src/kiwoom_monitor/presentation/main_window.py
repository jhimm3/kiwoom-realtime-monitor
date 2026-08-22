from __future__ import annotations

import json
import logging
import os
import ctypes
import sys
import time
from html import escape
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PySide6.QtGui import QCloseEvent, QResizeEvent, QShowEvent, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPolygon, QPalette
from PySide6.QtCore import QEvent, QEventLoop, QThread, QTimer, QUrl, QSize, QPoint, Signal
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
    QStackedLayout,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QLayout,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from PIL import Image, ImageOps, UnidentifiedImageError

from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.infrastructure.app_paths import AppPaths
from kiwoom_monitor.infrastructure.persistence.database import DEFAULT_SETTINGS
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.minute_history_worker import MinuteHistoryWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.fundamentals_worker import FundamentalsWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.new_high_worker import NewHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.ranking_worker import RankingWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.daily_high_worker import DailyHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.nxt_eligibility_worker import NxtEligibilityWorker
from kiwoom_monitor.application.daily_high_service import DailyHighTargets
from kiwoom_monitor.application.trade_strength import StockFundamentals, trade_strength_percent
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting, ColumnSettingsRepository
from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupError, SettingsBackupService
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupError, ThemeBackupService
from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncError, GoogleDriveSyncService
from kiwoom_monitor.infrastructure.excel.theme_repository import ThemeRepository as ExcelThemeRepository
from kiwoom_monitor.domain.theme_import import validate_theme_header, validate_theme_rows
from kiwoom_monitor.domain.theme_parser import parse_themes, theme_key
from kiwoom_monitor.domain.theme_text_import import parse_theme_text
from kiwoom_monitor.presentation.theme_colors import text_color
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import ApiProfiles, LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomApiError, KiwoomRestClient, KiwoomSettings
from kiwoom_monitor.domain.strength_level import strength_badge
from kiwoom_monitor.application.theme_matching import MatchedThemeRow, match_theme_rows
from kiwoom_monitor.application.theme_preview import preview_theme_changes
from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import ImageThemeOcrWorker
from kiwoom_monitor.infrastructure.krx.stock_catalog_worker import KrxStockCatalogWorker


class RankingLoader(Protocol):
    def load_top_stocks(self) -> tuple[object, ...]: ...


logger = logging.getLogger(__name__)

APP_VERSION = "1.1.5"
APP_COPYRIGHT = "Copyright 2026 크니. All rights reserved."


class ClickableLabel(QLabel):
    """클릭 동작을 지원하는 안내/요약 라벨."""

    def __init__(self, on_click: Callable[[], None], text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: object) -> None:
        if getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mouseReleaseEvent(event)  # type: ignore[arg-type]


class GoogleDriveSyncWorker(QThread):
    """Drive 통신만 별도 스레드에서 실행해 표·설정 창을 멈추지 않는다."""

    completed = Signal(str)
    metadata_received = Signal(str)
    failed = Signal(str)

    def __init__(self, service: GoogleDriveSyncService, operation: str, target: str, interactive: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._operation = operation
        self._target = target
        self._interactive = interactive

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            if self._operation == "metadata":
                self.metadata_received.emit(self._service.latest_modified_time(interactive=self._interactive, target=self._target))
                return
            action = self._service.upload if self._operation == "upload" else self._service.download
            self.completed.emit(action(interactive=self._interactive, target=self._target))
        except GoogleDriveSyncError as error:
            self.failed.emit(str(error))


class UpdateCheckWorker(QThread):
    """GitHub Release 확인을 별도 스레드에서 수행한다."""

    completed = Signal(str, str, str)
    failed = Signal(str)

    RELEASE_API_URL = "https://api.github.com/repos/jhimm3/kiwoom-realtime-monitor/releases/latest"

    def run(self) -> None:
        try:
            request = Request(self.RELEASE_API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "KiwoomMonitor"})
            with urlopen(request, timeout=10) as response:
                document = json.loads(response.read().decode("utf-8"))
            version = str(document.get("tag_name", "")).strip().lstrip("vV")
            page_url = str(document.get("html_url", "")).strip()
            if not version or not page_url:
                raise ValueError("릴리즈 버전 정보를 찾을 수 없습니다.")
            update_asset = next((str(asset.get("browser_download_url", "")) for asset in document.get("assets", ()) if str(asset.get("name", "")).startswith("KiwoomMonitor-Update-") and str(asset.get("name", "")).endswith(".zip")), "")
            self.completed.emit(version, update_asset, page_url)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            self.failed.emit(str(error))


class UpdateDownloadWorker(QThread):
    """부분 업데이트 ZIP을 사용자 임시 데이터 폴더로 내려받는다."""

    completed = Signal(str)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, url: str, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._url = url
        self._version = version

    def run(self) -> None:
        try:
            folder = AppPaths.for_current_user().data_dir.parent / "updates"
            folder.mkdir(parents=True, exist_ok=True)
            destination = folder / f"KiwoomMonitor-Update-{self._version}.zip"
            request = Request(self._url, headers={"User-Agent": "KiwoomMonitor"})
            with urlopen(request, timeout=30) as response, destination.open("wb") as stream:
                total = int(response.headers.get("Content-Length", "0"))
                received = 0
                while block := response.read(1024 * 1024):
                    if self.isInterruptionRequested():
                        destination.unlink(missing_ok=True)
                        return
                    stream.write(block)
                    received += len(block)
                    if total:
                        self.progress.emit(min(100, int(received * 100 / total)))
            self.completed.emit(str(destination))
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            self.failed.emit(str(error))


def choose_similar_stock(parent: QWidget, lookup: object, name: str) -> tuple[str, str] | None:
    finder = getattr(lookup, "find_stock_candidates", None)
    candidates = finder(name) if callable(finder) else ()
    if not candidates:
        return None
    labels = [f"{stock_name} ({code})" for code, stock_name in candidates]
    selected, ok = QInputDialog.getItem(
        parent,
        "비슷한 종목 선택",
        f"'{name}'와(과) 비슷한 종목입니다. 맞는 종목을 선택하세요.",
        labels,
        0,
        False,
    )
    if not ok:
        return None
    return candidates[labels.index(selected)]


class SimilarStockDialog(QDialog):
    def __init__(self, lookup: object, original_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lookup = lookup
        self._original_name = original_name
        self.cancelled_all = False
        self.setWindowTitle("비슷한 종목 선택")
        self.resize(420, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"'{original_name}'와 같은 종목명이 없습니다.\n전체 상장종목에서 검색하거나 후보를 선택하세요."))
        self._search = QLineEdit(original_name)
        self._search.setPlaceholderText("종목명 검색")
        layout.addWidget(self._search)
        self._list = QListWidget()
        layout.addWidget(self._list)
        actions = QHBoxLayout()
        self._select = QPushButton("선택")
        skip = QPushButton("이번 종목 무시")
        cancel = QPushButton("전체 취소")
        actions.addStretch(); actions.addWidget(self._select); actions.addWidget(skip); actions.addWidget(cancel)
        layout.addLayout(actions)
        self._select.clicked.connect(self.accept)
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        skip.clicked.connect(self.reject)
        cancel.clicked.connect(self._cancel_all)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._reload)
        self._search.textChanged.connect(lambda: self._timer.start())
        self._reload()

    def _cancel_all(self) -> None:
        self.cancelled_all = True
        self.reject()

    def _reload(self) -> None:
        finder = getattr(self._lookup, "find_stock_candidates", None)
        rows = finder(self._search.text()) if callable(finder) else ()
        self._list.clear()
        for code, name in rows:
            item = QListWidgetItem(f"{name} ({code})")
            item.setData(Qt.ItemDataRole.UserRole, (code, name))
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._select.setEnabled(self._list.count() > 0)

    @property
    def selected(self) -> tuple[str, str] | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


def choose_similar_stock(parent: QWidget, lookup: object, name: str) -> tuple[tuple[str, str] | None, bool]:
    dialog = SimilarStockDialog(lookup, name, parent)
    if dialog.exec():
        return dialog.selected, False
    return None, dialog.cancelled_all


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsRepository, api_path: Path | None = None, log_opener: Callable[[], None] | None = None, theme_manager_opener: Callable[[], None] | None = None, parent: QWidget | None = None, column_manager_opener: Callable[[], None] | None = None, backup_exporter: Callable[[], None] | None = None, backup_importer: Callable[[], None] | None = None, theme_manager_panel_factory: Callable[[QWidget], QWidget] | None = None, column_manager_panel_factory: Callable[[QWidget], QWidget] | None = None, stock_lookup: object | None = None, drive_connector: Callable[[], None] | None = None, drive_downloader: Callable[[], None] | None = None, drive_uploader: Callable[[], None] | None = None, drive_disconnector: Callable[[], None] | None = None, drive_status: Callable[[], str] | None = None, theme_backup_exporter: Callable[[], None] | None = None, theme_backup_importer: Callable[[], None] | None = None, drive_client_importer: Callable[[], None] | None = None, update_checker: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._api_path = api_path
        self._log_opener = log_opener
        self._theme_manager_opener = theme_manager_opener
        self._column_manager_opener = column_manager_opener
        self._backup_exporter = backup_exporter
        self._backup_importer = backup_importer
        self._theme_manager_panel_factory = theme_manager_panel_factory
        self._column_manager_panel_factory = column_manager_panel_factory
        self._stock_lookup = stock_lookup
        self._drive_connector = drive_connector
        self._drive_downloader = drive_downloader
        self._drive_uploader = drive_uploader
        self._drive_disconnector = drive_disconnector
        self._drive_status = drive_status
        self._drive_client_importer = drive_client_importer
        self._theme_backup_exporter = theme_backup_exporter
        self._theme_backup_importer = theme_backup_importer
        self._update_checker = update_checker
        self._drive_status_label: QLabel | None = None
        self._google_drive_auto_download = QCheckBox("앱 시작 시 자동 다운로드")
        self._google_drive_auto_download.setChecked(settings.get("google_drive_auto_download") == "1")
        self._google_drive_auto_upload = QCheckBox("변경 후 자동 업로드")
        self._google_drive_auto_upload.setChecked(settings.get("google_drive_auto_upload") == "1")
        self._google_drive_auto_upload_on_exit = QCheckBox("종료 시 자동 업로드 시도 (종료가 늦어질 수 있음)")
        self._google_drive_auto_upload_on_exit.setChecked(settings.get("google_drive_auto_upload_on_exit") == "1")
        self._google_drive_sync_target = QComboBox()
        self._google_drive_sync_target.addItem("설정과 테마", "both")
        self._google_drive_sync_target.addItem("설정만", "settings")
        self._google_drive_sync_target.addItem("테마만", "themes")
        saved_drive_target = settings.get("google_drive_sync_target")
        self._google_drive_sync_target.setCurrentIndex(("both", "settings", "themes").index(saved_drive_target) if saved_drive_target in {"both", "settings", "themes"} else 0)
        self._google_drive_auto_download.toggled.connect(self.refresh_drive_status)
        self._google_drive_auto_upload.toggled.connect(self.refresh_drive_status)
        self._google_drive_auto_upload_on_exit.toggled.connect(self.refresh_drive_status)
        self.api_changed = False
        self._dialog_size_save_timer = QTimer(self)
        self._dialog_size_save_timer.setSingleShot(True)
        self._dialog_size_save_timer.setInterval(350)
        self._dialog_size_save_timer.timeout.connect(self._save_dialog_size)
        self.setWindowTitle("기본 설정")

        self._rank_query_type = QComboBox()
        self._rank_query_type.addItem("30초", "5")
        self._rank_query_type.addItem("1분", "1")
        self._rank_query_type.addItem("10분", "2")
        self._rank_query_type.addItem("1시간", "3")
        self._rank_query_type.addItem("당일 누적", "4")
        saved_rank_query = settings.get("rank_query_type")
        self._rank_query_type.setCurrentIndex(("5", "1", "2", "3", "4").index(saved_rank_query) if saved_rank_query in {"1", "2", "3", "4", "5"} else 0)
        self._rank_row_colors = {
            "odd": settings.get("rank_row_odd_color"),
            "even": settings.get("rank_row_even_color"),
            "changed": settings.get("rank_changed_row_color"),
        }
        self._rank_row_color_buttons = {
            key: self._rank_row_color_button(key) for key in self._rank_row_colors
        }
        self._rank_changed_highlight_seconds = QDoubleSpinBox()
        self._rank_changed_highlight_seconds.setRange(0.0, 10.0)
        self._rank_changed_highlight_seconds.setSingleStep(0.1)
        self._rank_changed_highlight_seconds.setDecimals(2)
        self._rank_changed_highlight_seconds.setSuffix("초")
        try:
            self._rank_changed_highlight_seconds.setValue(float(settings.get("rank_changed_highlight_seconds")))
        except ValueError:
            self._rank_changed_highlight_seconds.setValue(2.0)
        self._rank_changed_highlight_enabled = QCheckBox("순위 변동 행 강조 사용")
        self._rank_changed_highlight_enabled.setChecked(settings.get("rank_changed_highlight_enabled") == "1")
        self._ui_mode = QComboBox()
        self._ui_mode.addItem("반응형 UI", "responsive")
        self._ui_mode.addItem("고정 UI / 공간 확장", "fixed")
        self._ui_mode.setCurrentIndex(0 if settings.get("ui_mode") == "responsive" else 1)
        periods = (("1m", "1분"), ("5m", "5분"), ("60m", "60분"), ("day", "1일"))
        self._strength_fields = {
            period: tuple(QLineEdit(settings.get(f"strength_{period}_{level}")) for level in ("interest", "caution", "fire"))
            for period, _ in periods
        }
        self._strength_period_labels = dict(periods)
        self._trade_value_alert_fields = {
            period: QLineEdit(settings.get(f"trade_value_{period}_alert_eok"))
            for period, _ in periods
        }
        self._trade_value_alert_enabled = QCheckBox("거래대금 강조 사용")
        self._trade_value_alert_enabled.setChecked(settings.get("trade_value_alert_enabled") == "1")
        self._near_high_fields = {level: QLineEdit(settings.get(f"near_high_{level}_percent")) for level in ("interest", "caution", "fire")}
        self._near_high_row_alert_level = QComboBox()
        self._near_high_row_alert_level.addItem("관심", "interest")
        self._near_high_row_alert_level.addItem("주의", "caution")
        self._near_high_row_alert_level.addItem("불", "fire")
        saved_row_level = settings.get("near_high_row_alert_level")
        self._near_high_row_alert_level.setCurrentIndex(("interest", "caution", "fire").index(saved_row_level) if saved_row_level in {"interest", "caution", "fire"} else 2)
        self._near_high_icons = QCheckBox("신고가 근접 단계 아이콘 표시")
        self._near_high_icons.setChecked(settings.get("near_high_show_icon") == "1")
        self._near_high_icon_fields = {level: QLineEdit(settings.get(f"near_high_icon_{level}")) for level in ("interest", "caution", "fire")}
        self._near_high_icon_images = {level: settings.get(f"near_high_icon_{level}_image") for level in ("interest", "caution", "fire")}
        self._near_high_icon_image_labels = {level: QLabel() for level in ("interest", "caution", "fire")}
        for level in self._near_high_icon_images:
            self._update_near_high_icon_image_label(level)
        self._near_high_sounds = QCheckBox("신고가 근접 단계 소리 사용")
        self._near_high_sounds.setChecked(settings.get("near_high_sound_enabled") == "1")
        self._near_high_sound_paths = {level: settings.get(f"near_high_sound_{level}") for level in ("interest", "caution", "fire")}
        self._near_high_sound_labels = {level: QLabel() for level in ("interest", "caution", "fire")}
        for level in self._near_high_sound_paths:
            self._update_near_high_sound_label(level)
        self._theme_separators=QLineEdit(settings.get("theme_custom_separators"))
        self._theme_separators.setPlaceholderText("기본 , / | ; 외에 추가할 문자")
        self._font_size=QLineEdit(settings.get("ui_font_size")); self._font_size.setPlaceholderText("0: 자동")
        self._row_height=QLineEdit(settings.get("ui_row_height")); self._row_height.setPlaceholderText("0: 자동")
        self._theme_badge_enabled = QCheckBox("테마 배지 표시")
        self._theme_badge_enabled.setChecked(settings.get("theme_badge_enabled") == "1")
        self._badge_font_size=QLineEdit(settings.get("theme_badge_font_size")); self._badge_font_size.setPlaceholderText("0: 자동")
        self._badge_padding=QLineEdit(settings.get("theme_badge_padding"))
        self._show_server_clock = QCheckBox("오른쪽 하단 시간 표시")
        self._show_server_clock.setChecked(settings.get("show_server_clock") == "1")
        self._theme_trade_summary_enabled = QCheckBox("상위 테마 거래대금 표시")
        self._theme_trade_summary_enabled.setChecked(settings.get("theme_trade_summary_enabled") == "1")
        self._theme_trade_summary_period = QComboBox()
        for label, value in (("1분", "1m"), ("5분", "5m"), ("60분", "60m"), ("1일", "day")):
            self._theme_trade_summary_period.addItem(label, value)
        saved_theme_summary_period = settings.get("theme_trade_summary_period")
        self._theme_trade_summary_period.setCurrentIndex(("1m", "5m", "60m", "day").index(saved_theme_summary_period) if saved_theme_summary_period in {"1m", "5m", "60m", "day"} else 3)
        self._theme_trade_summary_excluded_stocks = QLineEdit(settings.get("theme_trade_summary_excluded_stocks"))
        self._theme_trade_summary_excluded_stocks.setPlaceholderText("예: 삼성전자, SK하이닉스 (종목명 또는 코드)")
        self._theme_trade_summary_excluded_enabled = QCheckBox("제외 종목 반영 테마 거래대금 표시")
        self._theme_trade_summary_excluded_enabled.setChecked(settings.get("theme_trade_summary_excluded_enabled") == "1")
        self._market_cap_highlight_fields = {
            "low": QLineEdit(settings.get("market_cap_highlight_low_eok")),
            "middle": QLineEdit(settings.get("market_cap_highlight_middle_eok")),
            "high": QLineEdit(settings.get("market_cap_highlight_high_eok")),
        }
        for field in self._market_cap_highlight_fields.values():
            field.setPlaceholderText("0: 해당 단계 끔 · 10,000억 = 1조")
        self._market_cap_highlight_enabled = QCheckBox("시가총액 강조 사용")
        self._market_cap_highlight_enabled.setChecked(settings.get("market_cap_highlight_enabled") == "1")
        self._market_cap_highlight_badge_enabled = QCheckBox("시가총액 강조 배지 표시")
        self._market_cap_highlight_badge_enabled.setChecked(settings.get("market_cap_highlight_badge_enabled") == "1")
        self._market_cap_highlight_colors = {
            level: settings.get(f"market_cap_highlight_{level}_color")
            for level in ("low", "middle", "high")
        }
        self._market_cap_highlight_color_buttons = {
            level: self._market_cap_highlight_color_button(level)
            for level in self._market_cap_highlight_colors
        }
        self._market_cap_highlight_badge_colors = {
            level: settings.get(f"market_cap_highlight_{level}_badge_color")
            for level in ("low", "middle", "high")
        }
        self._market_cap_highlight_badge_color_buttons = {
            level: self._market_cap_highlight_badge_color_button(level)
            for level in self._market_cap_highlight_badge_colors
        }
        self._decimal_fields = {
            "change_rate": QComboBox(), "trade_value": QComboBox(),
            "strength": QComboBox(), "high_distance": QComboBox(),
        }
        for key, field in self._decimal_fields.items():
            field.addItems([str(value) for value in range(9 if key == "strength" else 5)])
            field.setCurrentText(settings.get(f"decimal_{key}"))
        self._near_high_enabled=QCheckBox("신고가 근접 강조 사용")
        self._near_high_enabled.setChecked(settings.get("near_high_alert_enabled") == "1")
        self._strength_icons=QCheckBox("거래강도 단계 아이콘 표시")
        self._strength_icons.setChecked(settings.get("strength_show_icon") == "1")
        self._strength_icon_fields = {level: QLineEdit(settings.get(f"strength_icon_{level}")) for level in ("interest", "caution", "fire")}
        self._strength_icon_images = {level: settings.get(f"strength_icon_{level}_image") for level in ("interest", "caution", "fire")}
        self._strength_icon_image_labels = {level: QLabel() for level in ("interest", "caution", "fire")}
        for level in self._strength_icon_images:
            self._update_strength_icon_image_label(level)
        self._strength_display_mode = QComboBox()
        self._strength_display_mode.addItem("실시간 (진행 중 분 포함)", "live")
        self._strength_display_mode.addItem("직전 완료 구간", "completed")
        self._strength_display_mode.setCurrentIndex(1 if settings.get("strength_display_mode") == "completed" else 0)
        self._high_distance_period=QComboBox(); self._high_distance_period.addItem("5일 신고가", "5"); self._high_distance_period.addItem("20일 신고가", "20"); self._high_distance_period.addItem("250일 신고가(52주 근사)", "250")
        saved_period=settings.get("high_distance_period"); self._high_distance_period.setCurrentIndex(("5", "20", "250").index(saved_period) if saved_period in ("5", "20", "250") else 2)

        self._build_grouped_layout()
        return
        layout = QFormLayout(self)
        layout.addRow("화면 갱신 주기(초)", self._refresh_interval)
        layout.addRow("화면 모드", self._ui_mode)
        layout.addRow("관심 기준(%)", self._interest); layout.addRow("주의 기준(%)", self._caution); layout.addRow("불 기준(%)", self._fire)
        layout.addRow("신고가 근접 기준(%)", self._near_high)
        layout.addRow("추가 테마 구분자", self._theme_separators)
        layout.addRow("표 글자 크기(0: 자동)", self._font_size)
        layout.addRow("표 행 높이(0: 자동)", self._row_height)
        layout.addRow("테마 배지 글자 크기(0: 자동)", self._badge_font_size)
        layout.addRow("테마 배지 여백", self._badge_padding)
        layout.addRow(self._near_high_enabled)
        layout.addRow(self._strength_icons)
        layout.addRow("신고가 거리 기준", self._high_distance_period)
        if self._api_path is not None:
            api_button = QPushButton("API 설정")
            api_button.clicked.connect(self._open_api_settings)
            layout.addRow("API", api_button)
        if self._log_opener is not None:
            log_button = QPushButton("로그 폴더 열기")
            log_button.clicked.connect(self._log_opener)
            layout.addRow("모니터링 로그", log_button)
        if self._theme_manager_opener is not None:
            theme_button = QPushButton("종목/테마 관리")
            theme_button.clicked.connect(self._theme_manager_opener)
            layout.addRow("종목/테마", theme_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("설정 저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _build_grouped_layout(self) -> None:
        self.resize(self._dialog_dimension("settings_dialog_width", 680), self._dialog_dimension("settings_dialog_height", 650))
        layout = QVBoxLayout(self)
        # 탭 중 가장 넓은 설정 행이 창의 최소 크기를 강제하지 않게 한다.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        # Qt가 모든 탭의 내용 크기를 합쳐 944×617 정도의 "권장 최소 크기"를
        # 만들면, Windows에서는 테두리를 잡는 즉시 창이 그 크기까지 튀어
        # 버린다. 실제로 허용할 최소 크기를 명시해 사용자가 원하는 높이까지
        # 자연스럽게 조절할 수 있게 한다.
        self.setMinimumSize(480, 360)
        tabs = QTabWidget()

        strength_tab = QWidget(); strength_form = QFormLayout(strength_tab)
        strength_form.addRow(self._section_title("거래대금 강조"))
        strength_form.addRow(self._trade_value_alert_enabled)
        strength_form.addRow(QLabel("거래대금 빨간 강조 기준(억원, 0: 끔)"))
        for period, label in self._strength_period_labels.items():
            strength_form.addRow(f"{label}(억)", self._trade_value_alert_fields[period])
        strength_form.addRow(self._section_separator())
        strength_form.addRow(self._section_title("거래강도 기준"))
        strength_form.addRow(QLabel("기간별 거래대금 차이를 반영해 각각 기준을 적용합니다. (%)"))
        strength_form.addRow(QLabel("각 거래대금 제목을 눌러 해당 기간의 거래대금·거래강도를 실시간/직전 완료 구간으로 전환합니다."))
        for period, label in self._strength_period_labels.items():
            interest, caution, fire = self._strength_fields[period]
            strength_form.addRow(f"{label} 관심 / 주의 / 불", self._strength_row(interest, caution, fire))
        strength_form.addRow(self._section_separator())
        strength_form.addRow(self._section_title("강도 아이콘"))
        strength_form.addRow(self._strength_icons)
        strength_form.addRow(QLabel("단계 아이콘: 문자를 바꾸거나 이미지 파일을 선택할 수 있습니다. 권장: 투명 PNG, 정사각형 32×32 또는 64×64"))
        for level, label in (("interest", "관심"), ("caution", "주의"), ("fire", "불")):
            strength_form.addRow(f"{label} 아이콘", self._strength_icon_row(level))
        strength_reset = QPushButton("이 탭 초기화")
        strength_reset.clicked.connect(self._reset_strength_settings)
        strength_form.addRow(strength_reset)
        tabs.addTab(strength_tab, "거래강도")

        high_tab = QWidget(); high_form = QFormLayout(high_tab)
        high_form.addRow(self._section_title("신고가 기준 및 강조"))
        high_form.addRow(self._near_high_enabled)
        high_form.addRow(QLabel("신고가와 신고가%에 함께 적용됩니다."))
        high_form.addRow("신고가 기준", self._high_distance_period)
        high_form.addRow("신고가 근접 관심 / 주의 / 불(%)", self._strength_row(self._near_high_fields["interest"], self._near_high_fields["caution"], self._near_high_fields["fire"]))
        high_form.addRow("전체 행 빨간 강조 단계", self._near_high_row_alert_level)
        high_form.addRow(self._section_separator())
        high_form.addRow(self._section_title("신고가 아이콘"))
        high_form.addRow(self._near_high_icons)
        high_form.addRow(QLabel("근접 아이콘: 문자를 바꾸거나 이미지 파일을 선택할 수 있습니다. 권장: 투명 PNG, 정사각형 32×32 또는 64×64"))
        for level, label in (("interest", "관심"), ("caution", "주의"), ("fire", "불")):
            high_form.addRow(f"{label} 아이콘", self._near_high_icon_row(level))
        high_form.addRow(self._section_separator())
        high_form.addRow(self._section_title("신고가 알림 소리"))
        high_form.addRow(self._near_high_sounds)
        high_form.addRow(QLabel("소리 파일: WAV·MP3·OGG·M4A, 파일당 5MB·30초 이하. 신고가에 가까워져 더 높은 단계에 새로 진입할 때만 재생됩니다."))
        for level, label in (("interest", "관심"), ("caution", "주의"), ("fire", "불")):
            high_form.addRow(f"{label} 소리", self._near_high_sound_row(level))
        high_reset = QPushButton("이 탭 초기화")
        high_reset.clicked.connect(self._reset_high_settings)
        high_form.addRow(high_reset)
        tabs.addTab(high_tab, "신고가")

        layout_tab = QWidget(); layout_form = QFormLayout(layout_tab)
        layout_form.addRow(self._section_title("화면 모드"))
        layout_form.addRow("화면 모드", self._ui_mode)
        layout_form.addRow(self._section_separator())
        layout_form.addRow(self._section_title("고정 UI : 표 크기 및 테마 표시"))
        layout_form.addRow(self._theme_badge_enabled)
        layout_form.addRow("표 글자 크기(0: 자동)", self._font_size)
        layout_form.addRow("행 높이(0: 자동)", self._row_height)
        layout_form.addRow("테마 배지 글자 크기(0: 자동)", self._badge_font_size)
        layout_form.addRow("테마 배지 여백", self._badge_padding)
        layout_form.addRow(self._section_separator())
        layout_form.addRow(self._section_title("순위 행 표시"))
        layout_form.addRow(self._rank_changed_highlight_enabled)
        layout_form.addRow("홀수 순위 배경", self._rank_row_color_buttons["odd"])
        layout_form.addRow("짝수 순위 배경", self._rank_row_color_buttons["even"])
        layout_form.addRow("순위 변동 행 배경", self._rank_row_color_buttons["changed"])
        layout_form.addRow("순위 변동 표시 시간", self._rank_changed_highlight_seconds)
        layout_reset = QPushButton("이 탭 초기화")
        layout_reset.clicked.connect(self._reset_ui_layout_settings)
        layout_form.addRow(layout_reset)
        tabs.addTab(layout_tab, "화면 구성")

        display_tab = QWidget(); display_form = QFormLayout(display_tab)
        display_form.addRow(self._section_title("상위 테마 거래대금"))
        display_form.addRow(self._theme_trade_summary_enabled)
        display_form.addRow(self._theme_trade_summary_excluded_enabled)
        display_form.addRow("테마 거래대금 기준", self._theme_trade_summary_period)
        display_form.addRow("제외 종목 목록", self._theme_trade_exclusion_row())
        display_form.addRow(self._section_separator())
        display_form.addRow(self._section_title("소수점 표시"))
        display_form.addRow("등락률 소수점", self._decimal_fields["change_rate"])
        display_form.addRow("거래대금 소수점", self._decimal_fields["trade_value"])
        display_form.addRow("거래강도 소수점", self._decimal_fields["strength"])
        display_form.addRow("신고가% 소수점", self._decimal_fields["high_distance"])
        display_form.addRow(self._section_separator())
        display_form.addRow(self._section_title("시가총액 강조 단계"))
        display_form.addRow(self._market_cap_highlight_enabled)
        display_form.addRow(self._market_cap_highlight_badge_enabled)
        display_form.addRow("1단계 기준(억)", self._market_cap_highlight_row("low"))
        display_form.addRow("2단계 기준(억)", self._market_cap_highlight_row("middle"))
        display_form.addRow("3단계 기준(억)", self._market_cap_highlight_row("high"))
        display_form.addRow(self._section_separator())
        display_form.addRow(self._section_title("오른쪽 하단 시각"))
        display_form.addRow(self._show_server_clock)
        display_reset = QPushButton("이 탭 초기화")
        display_reset.clicked.connect(self._reset_ui_display_settings)
        display_form.addRow(display_reset)
        tabs.addTab(display_tab, "표시 형식")

        if self._column_manager_panel_factory is not None:
            columns_tab = self._column_manager_panel_factory(tabs)
            columns_tab.setWindowFlags(Qt.WindowType.Widget)
        else:
            columns_tab = QWidget(); columns_form = QFormLayout(columns_tab)
            columns_form.addRow(QLabel("표에 표시할 항목을 한 번에 고르고, 표시 순서를 바꿉니다."))
            if self._column_manager_opener is not None:
                columns_button = QPushButton("필드 편집 열기")
                columns_button.clicked.connect(self._column_manager_opener)
                columns_form.addRow(columns_button)
        tabs.addTab(columns_tab, "필드 편집")

        if self._theme_manager_panel_factory is not None:
            theme_tab = self._theme_manager_panel_factory(tabs)
            theme_tab.setWindowFlags(Qt.WindowType.Widget)
            tabs.addTab(theme_tab, "종목/테마")

        manage_tab = QWidget(); manage_form = QFormLayout(manage_tab)
        # 이 두 탭은 별도 창을 키우지 않고, 현재 기본설정 창 안에서만
        # 배치되어야 한다. QFormLayout의 내용 기반 최소 크기 전파를 끈다.
        manage_form.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        manage_form.addRow(self._section_title("연결 및 기록"))
        if self._api_path is not None:
            api_button = QPushButton("API 설정")
            api_button.clicked.connect(self._open_api_settings)
            manage_form.addRow("API", api_button)
        if self._log_opener is not None:
            log_button = QPushButton("로그 폴더 열기")
            log_button.clicked.connect(self._log_opener)
            manage_form.addRow("모니터링 로그", log_button)
        if self._theme_manager_opener is not None and self._theme_manager_panel_factory is None:
            theme_button = QPushButton("종목/테마 관리")
            theme_button.clicked.connect(self._theme_manager_opener)
            manage_form.addRow("종목/테마", theme_button)
        manage_form.addRow(self._section_separator())
        manage_form.addRow(self._section_title("설정 백업 및 복원"))
        if self._backup_exporter is not None:
            backup_button = QPushButton("설정 백업 저장")
            backup_button.clicked.connect(self._backup_exporter)
            manage_form.addRow("설정 백업", backup_button)
        if self._backup_importer is not None:
            restore_button = QPushButton("설정 백업 불러오기")
            restore_button.clicked.connect(self._backup_importer)
            manage_form.addRow("설정 복원", restore_button)
        if self._theme_backup_exporter is not None:
            theme_backup_button = QPushButton("테마 DB 저장")
            theme_backup_button.clicked.connect(self._theme_backup_exporter)
            manage_form.addRow("테마 DB", theme_backup_button)
        if self._theme_backup_importer is not None:
            theme_restore_button = QPushButton("테마 DB 불러오기")
            theme_restore_button.clicked.connect(self._theme_backup_importer)
            manage_form.addRow("테마 DB 복원", theme_restore_button)
        if self._drive_connector is not None:
            manage_form.addRow(self._section_separator())
            manage_form.addRow(self._section_title("Google Drive 동기화"))
            manage_form.addRow(QLabel("테마와 일반 설정을 내 드라이브의 ‘키움 실시간 모니터’ 폴더에 동기화합니다."))
            status = QLabel()
            # 상태 문구가 바뀔 때 줄바꿈 높이까지 다시 계산하면 창 높이가
            # 튀는 원인이 된다. 상태는 한 줄로 표시한다.
            status.setWordWrap(False)
            self._drive_status_label = status
            self.refresh_drive_status()
            if self._drive_client_importer is not None:
                client_file = QPushButton("내 OAuth JSON 연결")
                client_file.clicked.connect(self._drive_client_importer)
                manage_form.addRow("OAuth 구성", client_file)
            connect = QPushButton("Google Drive 연결")
            connect.clicked.connect(self._drive_connector)
            manage_form.addRow("연결", connect)
            manage_form.addRow("상태", status)
            manage_form.addRow("동기화 대상", self._google_drive_sync_target)
            manage_form.addRow("자동 동기화", self._google_drive_auto_download)
            manage_form.addRow("자동 업로드", self._google_drive_auto_upload)
            manage_form.addRow("", self._google_drive_auto_upload_on_exit)
            if self._drive_downloader is not None:
                download = QPushButton("지금 다운로드")
                download.clicked.connect(self._drive_downloader)
                manage_form.addRow("가져오기", download)
            if self._drive_uploader is not None:
                upload = QPushButton("지금 업로드")
                upload.clicked.connect(self._drive_uploader)
                manage_form.addRow("보내기", upload)
            if self._drive_disconnector is not None:
                disconnect = QPushButton("연결 해제")
                disconnect.clicked.connect(self._drive_disconnector)
                manage_form.addRow("계정", disconnect)
        manage_tab.setMinimumSize(0, 0)
        tabs.addTab(manage_tab, "관리")

        info_tab = QWidget()
        info_layout = QVBoxLayout(info_tab)
        info_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        info_layout.addWidget(self._section_title("프로그램 정보"))
        app_info = QLabel(
            f"키움 실시간 종목 모니터 {APP_VERSION}\n"
            f"{APP_COPYRIGHT}\n"
            "이 프로그램은 여러 오픈소스 소프트웨어를 기반으로 제작되었습니다."
        )
        app_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_info.setWordWrap(False)
        app_info.setStyleSheet("color: #667085; padding: 18px 8px;")
        info_layout.addWidget(app_info)
        if self._update_checker is not None:
            update_check = QPushButton("업데이트 확인")
            update_check.clicked.connect(self._update_checker)
            info_layout.addWidget(update_check, alignment=Qt.AlignmentFlag.AlignHCenter)
        info_layout.addStretch()
        info_tab.setMinimumSize(0, 0)
        tabs.addTab(info_tab, "프로그램 정보")
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("설정 저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def minimumSizeHint(self) -> QSize:
        """탭의 긴 내용이 Windows 창 크기 제한으로 전파되는 것을 막는다."""
        return QSize(480, 360)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_dialog_size_save_timer"):
            self._dialog_size_save_timer.start()

    def done(self, result: int) -> None:
        self._save_dialog_size()
        super().done(result)

    def _save_dialog_size(self) -> None:
        self._settings.set("settings_dialog_width", str(self.width()))
        self._settings.set("settings_dialog_height", str(self.height()))

    def _dialog_dimension(self, key: str, default: int) -> int:
        try:
            return max(400, min(1600, int(self._settings.get(key))))
        except (TypeError, ValueError):
            return default

    def refresh_drive_status(self) -> None:
        if self._drive_status_label is not None:
            status = self._drive_status() if self._drive_status is not None else "연결되지 않음"
            if status.startswith("연결됨"):
                automatic = []
                if self._google_drive_auto_download.isChecked():
                    automatic.append("시작 시 다운로드")
                if self._google_drive_auto_upload.isChecked():
                    automatic.append("변경 후 업로드")
                if self._google_drive_auto_upload_on_exit.isChecked():
                    automatic.append("종료 시 업로드")
                status = "연결됨 · " + (", ".join(automatic) if automatic else "수동 동기화")
            self._drive_status_label.setText(status)

    def _reset_strength_settings(self) -> None:
        for period, fields in self._strength_fields.items():
            for level, field in zip(("interest", "caution", "fire"), fields):
                field.setText(DEFAULT_SETTINGS[f"strength_{period}_{level}"])
            self._trade_value_alert_fields[period].setText(DEFAULT_SETTINGS[f"trade_value_{period}_alert_eok"])
        self._trade_value_alert_enabled.setChecked(DEFAULT_SETTINGS["trade_value_alert_enabled"] == "1")
        self._strength_icons.setChecked(DEFAULT_SETTINGS["strength_show_icon"] == "1")
        self._strength_display_mode.setCurrentIndex(1 if DEFAULT_SETTINGS["strength_display_mode"] == "completed" else 0)
        for level, field in self._strength_icon_fields.items():
            field.setText(DEFAULT_SETTINGS[f"strength_icon_{level}"])
            self._strength_icon_images[level] = ""
            self._update_strength_icon_image_label(level)

    def _reset_high_settings(self) -> None:
        self._high_distance_period.setCurrentIndex(("5", "20", "250").index(DEFAULT_SETTINGS["high_distance_period"]))
        for level, field in self._near_high_fields.items():
            field.setText(DEFAULT_SETTINGS[f"near_high_{level}_percent"])
        self._near_high_row_alert_level.setCurrentIndex(("interest", "caution", "fire").index(DEFAULT_SETTINGS["near_high_row_alert_level"]))
        self._near_high_icons.setChecked(DEFAULT_SETTINGS["near_high_show_icon"] == "1")
        for level, field in self._near_high_icon_fields.items():
            field.setText(DEFAULT_SETTINGS[f"near_high_icon_{level}"])
            self._near_high_icon_images[level] = ""
            self._update_near_high_icon_image_label(level)
        self._near_high_sounds.setChecked(DEFAULT_SETTINGS["near_high_sound_enabled"] == "1")
        for level in self._near_high_sound_paths:
            self._near_high_sound_paths[level] = DEFAULT_SETTINGS[f"near_high_sound_{level}"]
            self._update_near_high_sound_label(level)
        self._near_high_enabled.setChecked(DEFAULT_SETTINGS["near_high_alert_enabled"] == "1")

    def _reset_ui_layout_settings(self) -> None:
        for key in self._rank_row_colors:
            self._rank_row_colors[key] = DEFAULT_SETTINGS[f"rank_row_{key}_color"] if key != "changed" else DEFAULT_SETTINGS["rank_changed_row_color"]
            self._update_rank_row_color_button(key)
        self._rank_changed_highlight_seconds.setValue(float(DEFAULT_SETTINGS["rank_changed_highlight_seconds"]))
        self._rank_changed_highlight_enabled.setChecked(DEFAULT_SETTINGS["rank_changed_highlight_enabled"] == "1")
        self._ui_mode.setCurrentIndex(0 if DEFAULT_SETTINGS["ui_mode"] == "responsive" else 1)
        self._font_size.setText(DEFAULT_SETTINGS["ui_font_size"])
        self._row_height.setText(DEFAULT_SETTINGS["ui_row_height"])
        self._theme_badge_enabled.setChecked(DEFAULT_SETTINGS["theme_badge_enabled"] == "1")
        self._badge_font_size.setText(DEFAULT_SETTINGS["theme_badge_font_size"])
        self._badge_padding.setText(DEFAULT_SETTINGS["theme_badge_padding"])

    def _reset_ui_display_settings(self) -> None:
        for level, field in self._market_cap_highlight_fields.items():
            field.setText(DEFAULT_SETTINGS[f"market_cap_highlight_{level}_eok"])
            self._market_cap_highlight_colors[level] = DEFAULT_SETTINGS[f"market_cap_highlight_{level}_color"]
            self._update_market_cap_highlight_color_button(level)
            self._market_cap_highlight_badge_colors[level] = DEFAULT_SETTINGS[f"market_cap_highlight_{level}_badge_color"]
            self._update_market_cap_highlight_badge_color_button(level)
        self._market_cap_highlight_enabled.setChecked(DEFAULT_SETTINGS["market_cap_highlight_enabled"] == "1")
        self._market_cap_highlight_badge_enabled.setChecked(DEFAULT_SETTINGS["market_cap_highlight_badge_enabled"] == "1")
        self._show_server_clock.setChecked(DEFAULT_SETTINGS["show_server_clock"] == "1")
        self._theme_trade_summary_enabled.setChecked(DEFAULT_SETTINGS["theme_trade_summary_enabled"] == "1")
        self._theme_trade_summary_period.setCurrentIndex(("1m", "5m", "60m", "day").index(DEFAULT_SETTINGS["theme_trade_summary_period"]))
        self._theme_trade_summary_excluded_stocks.setText(DEFAULT_SETTINGS["theme_trade_summary_excluded_stocks"])
        self._theme_trade_summary_excluded_enabled.setChecked(DEFAULT_SETTINGS["theme_trade_summary_excluded_enabled"] == "1")
        for key, field in self._decimal_fields.items():
            field.setCurrentText(DEFAULT_SETTINGS[f"decimal_{key}"])

    def _theme_trade_exclusion_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        find = QPushButton("종목 찾기")
        find.clicked.connect(self._find_theme_excluded_stock)
        layout.addWidget(self._theme_trade_summary_excluded_stocks)
        layout.addWidget(find)
        return widget

    def _find_theme_excluded_stock(self) -> None:
        if self._stock_lookup is None:
            QMessageBox.information(self, "종목 찾기", "전체 상장종목 목록을 사용할 수 없습니다.")
            return
        dialog = SimilarStockDialog(self._stock_lookup, "", self)
        if not dialog.exec() or dialog.selected is None:
            return
        _, name = dialog.selected
        existing = list(parse_themes(self._theme_trade_summary_excluded_stocks.text(), ",/|;"))
        if all("".join(name.split()).casefold() != "".join(value.split()).casefold() for value in existing):
            existing.append(name)
        self._theme_trade_summary_excluded_stocks.setText(", ".join(existing))

    @staticmethod
    def _strength_row(interest: QLineEdit, caution: QLineEdit, fire: QLineEdit) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for field in (interest, caution, fire):
            field.setMaximumWidth(90)
            layout.addWidget(field)
        layout.addStretch()
        return widget

    def _strength_icon_row(self, level: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        field = self._strength_icon_fields[level]
        field.setMaximumWidth(60)
        choose = QPushButton("이미지 선택")
        choose.clicked.connect(lambda: self._choose_strength_icon_image(level))
        clear = QPushButton("해제")
        clear.clicked.connect(lambda: self._clear_strength_icon_image(level))
        layout.addWidget(field)
        layout.addWidget(choose)
        layout.addWidget(clear)
        layout.addWidget(self._strength_icon_image_labels[level])
        layout.addStretch()
        return widget

    def _choose_strength_icon_image(self, level: str) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "강도 아이콘 이미지 선택", "", "이미지 파일 (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not source:
            return
        root = self._api_path.parent.parent if self._api_path is not None else Path(__file__).resolve().parents[3]
        destination = root / "data" / "strength_icons" / f"{level}.png"
        try:
            self._store_icon_image(Path(source), destination)
        except (OSError, ValueError, UnidentifiedImageError) as error:
            QMessageBox.warning(self, "이미지 저장", f"아이콘 이미지를 저장하지 못했습니다.\n{error}")
            return
        self._strength_icon_images[level] = str(destination.relative_to(root))
        self._update_strength_icon_image_label(level)

    def _clear_strength_icon_image(self, level: str) -> None:
        self._strength_icon_images[level] = ""
        self._update_strength_icon_image_label(level)

    def _update_strength_icon_image_label(self, level: str) -> None:
        value = self._strength_icon_images[level]
        self._strength_icon_image_labels[level].setText(Path(value).name if value else "문자 아이콘 사용")

    def _near_high_icon_row(self, level: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        field = self._near_high_icon_fields[level]
        field.setMaximumWidth(60)
        choose = QPushButton("이미지 선택")
        choose.clicked.connect(lambda: self._choose_near_high_icon_image(level))
        clear = QPushButton("해제")
        clear.clicked.connect(lambda: self._clear_near_high_icon_image(level))
        layout.addWidget(field); layout.addWidget(choose); layout.addWidget(clear)
        layout.addWidget(self._near_high_icon_image_labels[level]); layout.addStretch()
        return widget

    def _choose_near_high_icon_image(self, level: str) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "신고가 아이콘 이미지 선택", "", "이미지 파일 (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not source:
            return
        root = self._api_path.parent.parent if self._api_path is not None else Path(__file__).resolve().parents[3]
        destination = root / "data" / "near_high_icons" / f"{level}.png"
        try:
            self._store_icon_image(Path(source), destination)
        except (OSError, ValueError, UnidentifiedImageError) as error:
            QMessageBox.warning(self, "이미지 저장", f"아이콘 이미지를 저장하지 못했습니다.\n{error}")
            return
        self._near_high_icon_images[level] = str(destination.relative_to(root))
        self._update_near_high_icon_image_label(level)

    def _clear_near_high_icon_image(self, level: str) -> None:
        self._near_high_icon_images[level] = ""
        self._update_near_high_icon_image_label(level)

    def _update_near_high_icon_image_label(self, level: str) -> None:
        value = self._near_high_icon_images[level]
        self._near_high_icon_image_labels[level].setText(Path(value).name if value else "문자 아이콘 사용")

    def _near_high_sound_row(self, level: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        choose = QPushButton("소리 선택")
        choose.clicked.connect(lambda: self._choose_near_high_sound(level))
        clear = QPushButton("해제")
        clear.clicked.connect(lambda: self._clear_near_high_sound(level))
        layout.addWidget(choose); layout.addWidget(clear); layout.addWidget(self._near_high_sound_labels[level]); layout.addStretch()
        return widget

    def _choose_near_high_sound(self, level: str) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "신고가 알림 소리 선택", "", "소리 파일 (*.wav *.mp3 *.ogg *.m4a)")
        if not source:
            return
        source_path = Path(source)
        max_bytes = 5 * 1024 * 1024
        try:
            if source_path.stat().st_size > max_bytes:
                raise ValueError("소리 파일은 5MB 이하만 사용할 수 있습니다.")
            duration_ms = self._audio_duration_ms(source_path)
            if duration_ms > 30_000:
                raise ValueError("소리 길이는 30초 이하만 사용할 수 있습니다.")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "소리 선택", str(error))
            return
        root = self._api_path.parent.parent if self._api_path is not None else Path(__file__).resolve().parents[3]
        suffix = source_path.suffix.lower() or ".wav"
        destination = root / "data" / "near_high_sounds" / f"{level}{suffix}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source_path.read_bytes())
        except OSError as error:
            QMessageBox.warning(self, "소리 저장", f"소리 파일을 저장하지 못했습니다.\n{error}")
            return
        self._near_high_sound_paths[level] = str(destination.relative_to(root))
        self._near_high_sounds.setChecked(True)
        self._update_near_high_sound_label(level)

    def _clear_near_high_sound(self, level: str) -> None:
        self._near_high_sound_paths[level] = ""
        self._update_near_high_sound_label(level)

    def _update_near_high_sound_label(self, level: str) -> None:
        value = self._near_high_sound_paths[level]
        self._near_high_sound_labels[level].setText(Path(value).name if value else "선택 안 함")

    @staticmethod
    def _store_icon_image(source: Path, destination: Path) -> None:
        """아이콘은 PNG로 표준화하고 축소·압축한 2MB 이하 복사본만 보관한다."""
        if source.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("원본 이미지는 50MB 이하만 사용할 수 있습니다.")
        with Image.open(source) as opened:
            if opened.width * opened.height > 100_000_000:
                raise ValueError("이미지 해상도가 너무 큽니다.")
            image = ImageOps.exif_transpose(opened)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            else:
                image = image.copy()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp.png")
        try:
            for dimension in (512, 384, 256, 192, 128, 96, 64, 48, 32):
                candidate = image.copy()
                candidate.thumbnail((dimension, dimension), Image.Resampling.LANCZOS)
                candidate.save(temporary, format="PNG", optimize=True)
                if temporary.stat().st_size <= 2 * 1024 * 1024:
                    temporary.replace(destination)
                    return
            raise ValueError("압축 후에도 아이콘이 2MB를 초과합니다.")
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _audio_duration_ms(source: Path) -> int:
        """Qt 미디어 백엔드로 지원 형식의 길이를 확인한다."""
        player = QMediaPlayer()
        loop = QEventLoop()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        player.durationChanged.connect(lambda duration: loop.quit() if duration > 0 else None)
        player.mediaStatusChanged.connect(
            lambda status: loop.quit()
            if status == QMediaPlayer.MediaStatus.InvalidMedia
            else None
        )
        player.setSource(QUrl.fromLocalFile(str(source)))
        timeout.start(3_000)
        loop.exec()
        duration = player.duration()
        player.setSource(QUrl())
        if duration <= 0:
            raise ValueError("소리 길이를 확인할 수 없습니다. 지원되는 WAV·MP3·OGG·M4A 파일을 선택하세요.")
        return duration

    @staticmethod
    def _section_separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #B8B8B8; border: 0;")
        return line

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 700; color: #1F4E79; padding-top: 3px;")
        return label

    def _rank_row_color_button(self, key: str) -> QPushButton:
        button = QPushButton()
        button.clicked.connect(lambda: self._choose_rank_row_color(key))
        self._update_rank_row_color_button(key, button)
        return button

    def _update_rank_row_color_button(self, key: str, button: QPushButton | None = None) -> None:
        target = button or self._rank_row_color_buttons.get(key)
        if target is None:
            return
        color = self._rank_row_colors.get(key, "#FFFFFF")
        target.setText(color)
        target.setStyleSheet(f"background:{color}; border:1px solid #999; padding:4px 10px;")

    def _choose_rank_row_color(self, key: str) -> None:
        color = QColorDialog.getColor(
            QColor(self._rank_row_colors.get(key, "#FFFFFF")),
            self,
            "순위 행 배경색 선택",
        )
        if color.isValid():
            self._rank_row_colors[key] = color.name().upper()
            self._update_rank_row_color_button(key)

    def _market_cap_highlight_row(self, level: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._market_cap_highlight_fields[level])
        layout.addWidget(self._market_cap_highlight_color_buttons[level])
        layout.addWidget(self._market_cap_highlight_badge_color_buttons[level])
        layout.addStretch()
        return widget

    def _market_cap_highlight_color_button(self, level: str) -> QPushButton:
        button = QPushButton()
        button.clicked.connect(lambda: self._choose_market_cap_highlight_color(level))
        self._update_market_cap_highlight_color_button(level, button)
        return button

    def _update_market_cap_highlight_color_button(self, level: str, button: QPushButton | None = None) -> None:
        target = button or self._market_cap_highlight_color_buttons.get(level)
        if target is None:
            return
        color = self._market_cap_highlight_colors.get(level, "#333333")
        target.setText(f"글자색 {color}")
        target.setStyleSheet(f"color:{color}; background:#FFFFFF; border:1px solid {color}; padding:4px 10px;")

    def _choose_market_cap_highlight_color(self, level: str) -> None:
        color = QColorDialog.getColor(
            QColor(self._market_cap_highlight_colors.get(level, "#333333")),
            self,
            "시가총액 강조 글자색 선택",
        )
        if color.isValid():
            self._market_cap_highlight_colors[level] = color.name().upper()
            self._update_market_cap_highlight_color_button(level)

    def _market_cap_highlight_badge_color_button(self, level: str) -> QPushButton:
        button = QPushButton()
        button.clicked.connect(lambda: self._choose_market_cap_highlight_badge_color(level))
        self._update_market_cap_highlight_badge_color_button(level, button)
        return button

    def _update_market_cap_highlight_badge_color_button(self, level: str, button: QPushButton | None = None) -> None:
        target = button or self._market_cap_highlight_badge_color_buttons.get(level)
        if target is None:
            return
        color = self._market_cap_highlight_badge_colors.get(level, "#FFFFFF")
        target.setText(f"배지색 {color}")
        target.setStyleSheet(f"background:{color}; color:{text_color(color)}; border:1px solid #999; padding:4px 10px;")

    def _choose_market_cap_highlight_badge_color(self, level: str) -> None:
        color = QColorDialog.getColor(
            QColor(self._market_cap_highlight_badge_colors.get(level, "#FFFFFF")),
            self,
            "시가총액 강조 배지색 선택",
        )
        if color.isValid():
            self._market_cap_highlight_badge_colors[level] = color.name().upper()
            self._update_market_cap_highlight_badge_color_button(level)

    def _open_api_settings(self) -> None:
        if self._api_path is None:
            return
        dialog = ApiSettingsDialog(self._api_path, self)
        if dialog.exec():
            LocalApiConfig(self._api_path).save_profiles(dialog.values)
            self.api_changed = True

    def _save(self) -> None:
        try:
            strength_thresholds = {
                period: tuple(float(field.text()) for field in fields)
                for period, fields in self._strength_fields.items()
            }
            trade_value_alerts = {period: float(field.text()) for period, field in self._trade_value_alert_fields.items()}
            near_high_thresholds = tuple(float(self._near_high_fields[level].text()) for level in ("interest", "caution", "fire"))
        except ValueError:
            QMessageBox.warning(self, "입력 확인", "강도 기준과 신고가 근접 기준은 0 이상의 숫자로 입력하세요.")
            return
        if any(interest < 0 or caution < interest or fire < caution for interest, caution, fire in strength_thresholds.values()) or any(value < 0 for value in trade_value_alerts.values()) or not (near_high_thresholds[0] >= near_high_thresholds[1] >= near_high_thresholds[2] >= 0):
            QMessageBox.warning(self, "입력 확인", "강도 기준은 관심 ≤ 주의 ≤ 불, 신고가 근접 기준은 관심 ≥ 주의 ≥ 불 순서의 0 이상 숫자로 입력하세요.")
            return
        try:
            font_size, row_height, badge_font_size, badge_padding = (int(field.text()) for field in (self._font_size, self._row_height, self._badge_font_size, self._badge_padding))
            market_cap_highlights = {
                level: float(field.text() or "0")
                for level, field in self._market_cap_highlight_fields.items()
            }
        except ValueError:
            QMessageBox.warning(self, "입력 확인", "화면 크기 설정과 시가총액 강조 기준을 확인하세요.")
            return
        market_cap_active_thresholds = [market_cap_highlights[level] for level in ("low", "middle", "high") if market_cap_highlights[level] > 0]
        if not (0 <= font_size <= 30 and 0 <= row_height <= 100 and 0 <= badge_font_size <= 30 and 0 <= badge_padding <= 20 and all(value >= 0 for value in market_cap_highlights.values()) and market_cap_active_thresholds == sorted(market_cap_active_thresholds)):
            QMessageBox.warning(self, "입력 확인", "화면 크기 설정 범위를 확인하세요.")
            return
        self._settings.set("rank_query_type", str(self._rank_query_type.currentData()))
        self._settings.set("rank_row_odd_color", self._rank_row_colors["odd"])
        self._settings.set("rank_row_even_color", self._rank_row_colors["even"])
        self._settings.set("rank_changed_row_color", self._rank_row_colors["changed"])
        self._settings.set("rank_changed_highlight_seconds", f"{self._rank_changed_highlight_seconds.value():.2f}")
        self._settings.set("rank_changed_highlight_enabled", "1" if self._rank_changed_highlight_enabled.isChecked() else "0")
        self._settings.set("ui_mode", str(self._ui_mode.currentData()))
        for period, (interest, caution, fire) in strength_thresholds.items():
            self._settings.set(f"strength_{period}_interest", str(interest))
            self._settings.set(f"strength_{period}_caution", str(caution))
            self._settings.set(f"strength_{period}_fire", str(fire))
        for period, value in trade_value_alerts.items():
            self._settings.set(f"trade_value_{period}_alert_eok", str(value))
        self._settings.set("trade_value_alert_enabled", "1" if self._trade_value_alert_enabled.isChecked() else "0")
        for level, value in zip(("interest", "caution", "fire"), near_high_thresholds):
            self._settings.set(f"near_high_{level}_percent", str(value))
        self._settings.set("near_high_row_alert_level", str(self._near_high_row_alert_level.currentData()))
        self._settings.set("near_high_show_icon", "1" if self._near_high_icons.isChecked() else "0")
        for level, field in self._near_high_icon_fields.items():
            self._settings.set(f"near_high_icon_{level}", field.text().strip())
            self._settings.set(f"near_high_icon_{level}_image", self._near_high_icon_images[level])
        self._settings.set("near_high_sound_enabled", "1" if self._near_high_sounds.isChecked() else "0")
        for level, value in self._near_high_sound_paths.items():
            self._settings.set(f"near_high_sound_{level}", value)
        self._settings.set("ui_font_size", str(font_size)); self._settings.set("ui_row_height", str(row_height)); self._settings.set("theme_badge_enabled", "1" if self._theme_badge_enabled.isChecked() else "0"); self._settings.set("theme_badge_font_size", str(badge_font_size)); self._settings.set("theme_badge_padding", str(badge_padding))
        for level, value in market_cap_highlights.items():
            self._settings.set(f"market_cap_highlight_{level}_eok", str(value))
            self._settings.set(f"market_cap_highlight_{level}_color", self._market_cap_highlight_colors[level])
            self._settings.set(f"market_cap_highlight_{level}_badge_color", self._market_cap_highlight_badge_colors[level])
        self._settings.set("market_cap_highlight_enabled", "1" if self._market_cap_highlight_enabled.isChecked() else "0")
        self._settings.set("market_cap_highlight_badge_enabled", "1" if self._market_cap_highlight_badge_enabled.isChecked() else "0")
        for key, field in self._decimal_fields.items():
            self._settings.set(f"decimal_{key}", field.currentText())
        self._settings.set("near_high_alert_enabled", "1" if self._near_high_enabled.isChecked() else "0")
        self._settings.set("strength_show_icon", "1" if self._strength_icons.isChecked() else "0")
        for level, field in self._strength_icon_fields.items():
            self._settings.set(f"strength_icon_{level}", field.text().strip())
            self._settings.set(f"strength_icon_{level}_image", self._strength_icon_images[level])
        self._settings.set("strength_display_mode", str(self._strength_display_mode.currentData()))
        self._settings.set("show_server_clock", "1" if self._show_server_clock.isChecked() else "0")
        self._settings.set("theme_trade_summary_enabled", "1" if self._theme_trade_summary_enabled.isChecked() else "0")
        self._settings.set("theme_trade_summary_period", str(self._theme_trade_summary_period.currentData()))
        self._settings.set("theme_trade_summary_excluded_stocks", self._theme_trade_summary_excluded_stocks.text().strip())
        self._settings.set("theme_trade_summary_excluded_enabled", "1" if self._theme_trade_summary_excluded_enabled.isChecked() else "0")
        self._settings.set("google_drive_auto_download", "1" if self._google_drive_auto_download.isChecked() else "0")
        self._settings.set("google_drive_auto_upload", "1" if self._google_drive_auto_upload.isChecked() else "0")
        self._settings.set("google_drive_auto_upload_on_exit", "1" if self._google_drive_auto_upload_on_exit.isChecked() else "0")
        self._settings.set("google_drive_sync_target", str(self._google_drive_sync_target.currentData()))
        self._settings.set("high_distance_period", str(self._high_distance_period.currentData()))
        self.accept()


class ColumnManagerDialog(QDialog):
    """기본 설정에서 여러 열의 표시 여부와 순서를 한 번에 편집한다."""

    def __init__(self, repository: ColumnSettingsRepository, columns: tuple[tuple[str, str], ...], table: QTableWidget, parent: QWidget | None = None, embedded: bool = False, on_applied: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self._repository = repository
        self._columns = columns
        self._table = table
        self._embedded = embedded
        self._on_applied = on_applied
        self._bulk_editing = False
        self.setWindowTitle("필드 편집")
        self.resize(360, 440)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("체크·전체 표시·순서 변경은 즉시 표에 반영됩니다. 항목을 위아래로 드래그해 순서를 바꿀 수 있습니다."))
        self._list = QListWidget()
        self._list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        header = table.horizontalHeader()
        for logical in sorted(range(len(columns)), key=header.visualIndex):
            _, label = columns[logical]
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, logical)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if not table.isColumnHidden(logical) else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        if embedded:
            self._list.itemChanged.connect(lambda _item: self._save_if_embedded())
            self._list.model().rowsMoved.connect(lambda *_args: self._save_if_embedded())
        layout.addWidget(self._list)

        tools = QHBoxLayout()
        show_all = QPushButton("전체 표시")
        show_all.clicked.connect(lambda: self._set_all_checked(True))
        hide_all = QPushButton("전체 숨김")
        hide_all.clicked.connect(lambda: self._set_all_checked(False))
        up = QPushButton("▲ 위로")
        up.clicked.connect(lambda: self._move_current(-1))
        down = QPushButton("▼ 아래로")
        down.clicked.connect(lambda: self._move_current(1))
        for button in (show_all, hide_all, up, down):
            tools.addWidget(button)
        layout.addLayout(tools)

        if not embedded:
            buttons = QDialogButtonBox()
            apply = buttons.addButton("적용", QDialogButtonBox.ButtonRole.ApplyRole)
            apply.clicked.connect(self._save)
            cancel = buttons.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
            cancel.clicked.connect(self.reject)
            layout.addWidget(buttons)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._bulk_editing = True
        for row in range(self._list.count()):
            self._list.item(row).setCheckState(state)
        self._bulk_editing = False
        self._save_if_embedded()

    def _move_current(self, offset: int) -> None:
        row = self._list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self._list.count():
            return
        item = self._list.takeItem(row)
        self._list.insertItem(target, item)
        self._list.setCurrentRow(target)
        self._save_if_embedded()

    def _save_if_embedded(self) -> None:
        if self._embedded and not self._bulk_editing:
            self._save()

    def _save(self) -> None:
        settings: list[ColumnSetting] = []
        for position in range(self._list.count()):
            item = self._list.item(position)
            logical = int(item.data(Qt.ItemDataRole.UserRole))
            name, _ = self._columns[logical]
            settings.append(ColumnSetting(name, item.checkState() == Qt.CheckState.Checked, position, self._table.columnWidth(logical)))
        if not any(setting.visible for setting in settings):
            QMessageBox.warning(self, "필드 편집", "최소 한 개의 열은 표시해야 합니다.")
            return
        self._repository.save(tuple(settings))
        if self._on_applied is not None:
            self._on_applied()
        if not self._embedded:
            self.accept()


class ApiSettingsDialog(QDialog):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("키움 API 설정")
        self._path = path
        self._mock_app_key = QLineEdit()
        self._mock_secret_key = QLineEdit()
        self._real_app_key = QLineEdit()
        self._real_secret_key = QLineEdit()
        for field in (self._mock_app_key, self._mock_secret_key, self._real_app_key, self._real_secret_key):
            field.setEchoMode(QLineEdit.EchoMode.Password)
        self._environment = QComboBox()
        self._environment.addItem("모의투자", "mock")
        self._environment.addItem("실전투자", "real")

        if path.exists():
            try:
                profiles = LocalApiConfig(path).load_profiles()
                self._mock_app_key.setText(profiles.mock_app_key)
                self._mock_secret_key.setText(profiles.mock_secret_key)
                self._real_app_key.setText(profiles.real_app_key)
                self._real_secret_key.setText(profiles.real_secret_key)
                self._environment.setCurrentIndex(0 if profiles.active_environment == "mock" else 1)
            except ValueError:
                pass

        layout = QFormLayout(self)
        layout.addRow("모의 App Key", self._mock_app_key)
        layout.addRow("모의 Secret Key", self._mock_secret_key)
        layout.addRow("실전 App Key", self._real_app_key)
        layout.addRow("실전 Secret Key", self._real_secret_key)
        layout.addRow("이번 실행 환경", self._environment)
        self._connection_status = QLabel("연결 확인 전")
        test_button = QPushButton("연결 테스트")
        test_button.clicked.connect(self._test_connection)
        test_row = QHBoxLayout()
        test_row.addWidget(test_button)
        test_row.addWidget(self._connection_status)
        layout.addRow("API 연결", test_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @property
    def values(self) -> ApiProfiles:
        return ApiProfiles(
            mock_app_key=self._mock_app_key.text().strip(), mock_secret_key=self._mock_secret_key.text().strip(),
            real_app_key=self._real_app_key.text().strip(), real_secret_key=self._real_secret_key.text().strip(),
            active_environment=str(self._environment.currentData()),
        )

    def _validate_and_accept(self) -> None:
        profiles = self.values
        active_pair = (profiles.real_app_key, profiles.real_secret_key) if profiles.active_environment == "real" else (profiles.mock_app_key, profiles.mock_secret_key)
        if not all(active_pair):
            QMessageBox.warning(self, "입력 확인", "이번 실행 환경의 App Key와 Secret Key를 모두 입력하세요.")
            return
        self.accept()

    def _test_connection(self) -> None:
        profiles = self.values
        app_key, secret_key = (
            (profiles.real_app_key, profiles.real_secret_key)
            if profiles.active_environment == "real"
            else (profiles.mock_app_key, profiles.mock_secret_key)
        )
        if not app_key or not secret_key:
            QMessageBox.warning(self, "입력 확인", "선택한 환경의 App Key와 Secret Key를 모두 입력하세요.")
            return
        self._connection_status.setText("연결 중…")
        self._connection_status.setStyleSheet("color: #B36B00; font-weight: bold;")
        QApplication.processEvents()
        try:
            KiwoomRestClient(KiwoomSettings(app_key, secret_key, profiles.active_environment)).get_access_token()
        except (KiwoomApiError, KeyError) as error:
            self._connection_status.setText("연결 실패")
            self._connection_status.setStyleSheet("color: #C00000; font-weight: bold;")
            QMessageBox.warning(self, "API 연결 테스트", f"연결에 실패했습니다.\n{error}")
        else:
            self._connection_status.setText("연결 성공")
            self._connection_status.setStyleSheet("color: #008000; font-weight: bold;")


class AlertSettingsDialog(QDialog):
    def __init__(self, settings: SettingsRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent); self._settings = settings; self.setWindowTitle("알림 설정")
        self._enabled = QCheckBox("신고가 근접 강조 사용")
        self._enabled.setChecked(settings.get("near_high_alert_enabled") == "1")
        self._thresholds = {level: QLineEdit(settings.get(f"near_high_{level}_percent")) for level in ("interest", "caution", "fire")}
        layout = QFormLayout(self); layout.addRow(self._enabled); layout.addRow("관심 / 주의 / 불(%)", SettingsDialog._strength_row(self._thresholds["interest"], self._thresholds["caution"], self._thresholds["fire"]))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save); buttons.rejected.connect(self.reject); layout.addRow(buttons)

    def _save(self) -> None:
        try: thresholds = tuple(float(self._thresholds[level].text()) for level in ("interest", "caution", "fire"))
        except ValueError: thresholds = (-1.0, -1.0, -1.0)
        if not (thresholds[0] >= thresholds[1] >= thresholds[2] >= 0):
            QMessageBox.warning(self, "입력 확인", "신고가 근접 기준은 관심 ≥ 주의 ≥ 불 순서의 0 이상 숫자로 입력하세요.")
            return
        self._settings.set("near_high_alert_enabled", "1" if self._enabled.isChecked() else "0")
        for level, value in zip(("interest", "caution", "fire"), thresholds):
            self._settings.set(f"near_high_{level}_percent", str(value))
        self.accept()


class ThemePreviewDialog(QDialog):
    def __init__(self, changes: tuple[object, ...], skipped: int, parent: QWidget | None = None, excluded_theme_keys: frozenset[str] = frozenset()) -> None:
        super().__init__(parent)
        self.setWindowTitle("테마 변경 미리보기")
        self.resize(840, 460)
        self.setMinimumSize(680, 360)
        self.setSizeGripEnabled(True)
        self._excluded_theme_keys = excluded_theme_keys
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"전체 {len(changes) + skipped} · 오류/제외 {skipped}\n기본은 테마 추가입니다. 각 행에서 추가 또는 변경을 고르고, 적용 테마 칸을 직접 수정할 수 있습니다."))
        table = QTableWidget(len(changes), 5)
        table.setHorizontalHeaderLabels(("종목", "기존 테마", "적용 방식", "적용 테마", "상태"))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((150, 190, 120, 270, 110)):
            table.setColumnWidth(column, width)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._changes = tuple(changes)
        for index, change in enumerate(changes):
            read_only_items = (
                (0, str(getattr(change, "name", ""))),
                (1, ", ".join(getattr(change, "before", ()))),
            )
            for column, text in read_only_items:
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(index, column, item)
            mode = QComboBox()
            mode.addItem("테마 추가", "append")
            mode.addItem("테마 변경", "replace")
            mode.currentIndexChanged.connect(lambda _, row=index: self._update_operation_themes(row))
            table.setCellWidget(index, 2, mode)
            applied = self._without_excluded(self._merged_themes(getattr(change, "before", ()), getattr(change, "after", ())))
            table.setItem(index, 3, QTableWidgetItem(", ".join(applied)))
            status = QTableWidgetItem(self._status_for(getattr(change, "before", ()), applied, "append"))
            status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(index, 4, status)
        self._table = table
        layout.addWidget(table)
        actions = QHBoxLayout()
        actions.addStretch()
        apply = QPushButton("적용")
        cancel = QPushButton("취소")
        apply.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        actions.addWidget(apply)
        actions.addWidget(cancel)
        layout.addLayout(actions)

    def changes(self, separators: str) -> tuple[object, ...]:
        edited: list[object] = []
        for index, change in enumerate(self._changes):
            item = self._table.item(index, 3)
            after = self._without_excluded(parse_themes(item.text() if item else "", separators))
            before = tuple(getattr(change, "before", ()))
            mode = self._table.cellWidget(index, 2)
            status = self._status_for(before, after, str(mode.currentData()) if mode is not None else "replace")
            edited.append(replace(change, after=after, status=status))
        return tuple(edited)

    def _update_operation_themes(self, row: int) -> None:
        change = self._changes[row]
        mode = self._table.cellWidget(row, 2)
        after = tuple(getattr(change, "after", ())) if mode is None or mode.currentData() == "replace" else self._merged_themes(getattr(change, "before", ()), getattr(change, "after", ()))
        after = self._without_excluded(after)
        item = self._table.item(row, 3)
        if item is not None:
            item.setText(", ".join(after))
        status = self._table.item(row, 4)
        if status is not None:
            status.setText(self._status_for(getattr(change, "before", ()), after, str(mode.currentData()) if mode is not None else "replace"))

    @staticmethod
    def _merged_themes(before: object, imported: object) -> tuple[str, ...]:
        values: list[str] = []
        for theme in tuple(before) + tuple(imported):
            if all(str(theme).casefold() != existing.casefold() for existing in values):
                values.append(str(theme))
        return tuple(values)

    def _without_excluded(self, themes: object) -> tuple[str, ...]:
        return tuple(str(theme) for theme in themes if theme_key(str(theme)) not in self._excluded_theme_keys)

    @staticmethod
    def _status_for(before: object, after: object, mode: str = "replace") -> str:
        before_values = tuple(str(value) for value in before)
        after_values = tuple(str(value) for value in after)
        if frozenset(value.casefold() for value in before_values) == frozenset(value.casefold() for value in after_values):
            return "변경 없음"
        return "신규" if not before_values else ("테마 추가" if mode == "append" else "테마 변경")


class ImageThemeRowsDialog(QDialog):
    def __init__(self, rows: tuple[object, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("이미지 테마 OCR 수정")
        self.resize(680, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("OCR 결과를 수정하세요. 행 추가·삭제가 가능하며, Excel의 종목명·테마 두 열을 복사해 Ctrl+V로 한 번에 붙여넣을 수 있습니다."))
        self.table = QTableWidget(len(rows), 2)
        self.table.setHorizontalHeaderLabels(("종목명", "테마"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        for index, row in enumerate(rows):
            self.table.setItem(index, 0, QTableWidgetItem(str(getattr(row, "name", ""))))
            self.table.setItem(index, 1, QTableWidgetItem(str(getattr(row, "themes", ""))))
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        add = QPushButton("행 추가")
        remove = QPushButton("선택 행 삭제")
        add.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()) if self.table.currentRow() >= 0 else None)
        actions.addWidget(add); actions.addWidget(remove); actions.addStretch()
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def eventFilter(self, watched: object, event: object) -> bool:
        if watched in (self.table, self.table.viewport()) and getattr(event, "type", lambda: None)() == QEvent.Type.KeyPress:
            if getattr(event, "matches", lambda _: False)(QKeySequence.StandardKey.Paste):
                self._paste_rows_from_clipboard()
                return True
        return super().eventFilter(watched, event)  # type: ignore[arg-type]

    def _paste_rows_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            return
        start_row = max(0, self.table.currentRow())
        for offset, line in enumerate(text.splitlines()):
            values = [value.strip() for value in line.split("\t")]
            if not any(values):
                continue
            row = start_row + offset
            while row >= self.table.rowCount():
                self.table.insertRow(self.table.rowCount())
            self.table.setItem(row, 0, QTableWidgetItem(values[0] if values else ""))
            self.table.setItem(row, 1, QTableWidgetItem(values[1] if len(values) > 1 else ""))
        self.table.setCurrentCell(start_row, 0)

    def rows(self) -> tuple[tuple[str, str], ...]:
        values = []
        for index in range(self.table.rowCount()):
            name = self.table.item(index, 0)
            themes = self.table.item(index, 1)
            if name and name.text().strip() and themes and themes.text().strip():
                values.append((name.text().strip(), themes.text().strip()))
        return tuple(values)


class TextThemeImportDialog(QDialog):
    def __init__(self, settings: SettingsRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("텍스트 테마 업데이트")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("테마/종목 목록을 그대로 붙여넣으세요. 🔥 뒤 제목을 테마로 적용하고, #소분류 이름은 별도 테마로 추가하지 않습니다."))
        rules = QFormLayout()
        self._heading_marker = QLineEdit(settings.get("theme_text_heading_marker"))
        self._heading_marker.setPlaceholderText("기본: 🔥 · 예: ⭐ 또는 테마:")
        rules.addRow("텍스트 테마 시작 표시", self._heading_marker)
        layout.addLayout(rules)
        self._text = QTextEdit()
        self._update_placeholder()
        self._heading_marker.textChanged.connect(self._save_heading_marker)
        layout.addWidget(self._text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("미리보기")
        example_button = buttons.addButton("예시 넣기", QDialogButtonBox.ButtonRole.ActionRole)
        example_button.clicked.connect(self._insert_example)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save_heading_marker(self, value: str) -> None:
        self._settings.set("theme_text_heading_marker", value.strip() or "🔥")
        self._update_placeholder()

    def _update_placeholder(self) -> None:
        marker = self._heading_marker.text().strip() or "🔥"
        self._text.setPlaceholderText(
            "예:\n"
            f"{marker}반도체\n"
            "삼성전자, SK하이닉스, 네패스\n\n"
            f"{marker}바이오\n"
            "삼양바이오팜, 나이벡, 에스티팜\n\n"
            f"{marker}스테이블코인\n"
            "SK증권, 다날, 카카오페이"
        )

    def _insert_example(self) -> None:
        marker = self._heading_marker.text().strip() or "🔥"
        self._text.setPlainText(
            f"{marker}반도체\n"
            "삼성전자, SK하이닉스, 네패스\n\n"
            f"{marker}바이오\n"
            "삼양바이오팜, 나이벡, 에스티팜\n\n"
            f"{marker}스테이블코인\n"
            "SK증권, 다날, 카카오페이"
        )

    @property
    def text(self) -> str:
        return self._text.toPlainText()


class ThemeEditDialog(QDialog):
    """테마를 배지 형태로 바로 추가·삭제하는 편집창."""

    def __init__(self, stock_name: str, themes: tuple[str, ...], separators: str = ",/|;", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("테마 편집")
        self.resize(520, 230)
        self.setMinimumSize(380, 180)
        self._themes = list(themes)
        self._separators = separators
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{stock_name}의 테마"))
        self._badges = QHBoxLayout()
        self._badges.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._badges)
        entry = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("추가할 테마를 입력하세요 (설정한 구분자 사용 가능)")
        add = QPushButton("추가")
        add.clicked.connect(self._add_themes)
        self._input.returnPressed.connect(self._add_themes)
        entry.addWidget(self._input)
        entry.addWidget(add)
        layout.addLayout(entry)
        edit = QPushButton("기존 테마 수정…")
        edit.setToolTip("기존 테마 이름을 바꿉니다. 새 테마는 위 입력칸에서 추가하세요.")
        edit.clicked.connect(self._edit_theme)
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._render_badges()

    @property
    def themes(self) -> tuple[str, ...]:
        return tuple(self._themes)

    def _add_themes(self) -> None:
        for theme in parse_themes(self._input.text(), self._separators):
            if theme not in self._themes:
                self._themes.append(theme)
        self._input.clear()
        self._render_badges()

    def _remove_theme(self, theme: str) -> None:
        self._themes.remove(theme)
        self._render_badges()

    def _edit_theme(self) -> None:
        if not self._themes:
            return
        before, ok = QInputDialog.getItem(self, "기존 테마 수정", "수정할 테마", self._themes, 0, False)
        if not ok:
            return
        after, ok = QInputDialog.getText(self, "기존 테마 수정", "새 테마 이름", text=before)
        if not ok or not after.strip():
            return
        replacement = parse_themes(after, self._separators)
        if len(replacement) != 1:
            QMessageBox.warning(self, "입력 확인", "수정할 테마는 하나만 입력하세요.")
            return
        value = replacement[0]
        index = self._themes.index(before)
        if value.casefold() != before.casefold() and any(theme.casefold() == value.casefold() for theme in self._themes):
            QMessageBox.warning(self, "입력 확인", "이미 있는 테마입니다.")
            return
        self._themes[index] = value
        self._render_badges()

    def _render_badges(self) -> None:
        while self._badges.count():
            item = self._badges.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for theme in self._themes:
            badge = QPushButton(f"{theme}  ×")
            badge.setToolTip("클릭하면 이 테마를 삭제합니다")
            badge.clicked.connect(lambda _, value=theme: self._remove_theme(value))
            self._badges.addWidget(badge)
        self._badges.addStretch()


class ThemeColorDialog(QDialog):
    PALETTE = (
        "#DCE6F1", "#FFF2CC", "#E2F0D9", "#FCE4D6", "#E4DFEC",
        "#F4CCCC", "#D9EAD3", "#CFE2F3", "#D9D2E9", "#FCE5CD",
        "#B4C7E7", "#FFD966", "#A9D18E", "#F4B183", "#C9B1D4",
        "#EA9999", "#93C47D", "#9FC5E8", "#B4A7D6", "#F6B26B",
    )

    def __init__(self, theme: str, current: str, parent: QWidget | None = None, *, allow_stock_only: bool = True) -> None:
        super().__init__(parent); self.setWindowTitle("테마 색상 변경"); self._color = current
        layout=QVBoxLayout(self); layout.addWidget(QLabel(f"테마: {theme}"))
        self._stock_only=QRadioButton("이 종목만"); self._all_stocks=QRadioButton("이 테마 전체"); self._all_stocks.setChecked(True)
        if allow_stock_only:
            layout.addWidget(self._stock_only)
        layout.addWidget(self._all_stocks)
        colors=QGridLayout(); layout.addLayout(colors)
        for index, color in enumerate(self.PALETTE):
            button=QPushButton(); button.setFixedSize(28,28); button.setStyleSheet(f"background:{color}; border: 1px solid #888;")
            button.setToolTip(color)
            button.clicked.connect(lambda _, value=color: self._choose(value)); colors.addWidget(button, index // 5, index % 5)
        custom = QPushButton("전체 색상 선택…")
        custom.clicked.connect(self._choose_custom_color)
        layout.addWidget(custom)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _choose(self, color: str) -> None: self._color=color
    def _choose_custom_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "테마 색상 선택")
        if color.isValid():
            self._color = color.name()
    @property
    def color(self) -> str: return self._color
    @property
    def stock_only(self) -> bool: return self._stock_only.isChecked()


class ThemeBulkDeleteDialog(QDialog):
    def __init__(self, themes: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("테마 일괄 삭제")
        self.resize(380, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("삭제할 테마를 체크하세요. 선택한 테마는 모든 종목에서 제거됩니다."))
        self._list = QListWidget()
        for theme in themes:
            item = QListWidgetItem(theme)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._list.addItem(item)
        layout.addWidget(self._list)
        select_all = QPushButton("전체 선택 / 해제")
        select_all.clicked.connect(self._toggle_all)
        layout.addWidget(select_all)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("선택 테마 삭제")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def themes(self) -> tuple[str, ...]:
        return tuple(self._list.item(index).text() for index in range(self._list.count()) if self._list.item(index).checkState() == Qt.CheckState.Checked)

    def _toggle_all(self) -> None:
        checked = any(self._list.item(index).checkState() == Qt.CheckState.Checked for index in range(self._list.count()))
        state = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
        for index in range(self._list.count()):
            self._list.item(index).setCheckState(state)


class ThemeBulkEditDialog(QDialog):
    def __init__(self, themes: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("테마 일괄 수정 미리보기")
        self.resize(600, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("새 테마명 칸에서 수정할 테마만 바꾸세요. 같은 이름으로 바꾸면 하나의 테마로 합쳐지며, 비우면 모든 종목에서 삭제됩니다."))
        self._table = QTableWidget(len(themes), 2)
        self._table.setHorizontalHeaderLabels(("기존 테마", "새 테마명"))
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, theme in enumerate(themes):
            before = QTableWidgetItem(theme)
            before.setFlags(before.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, before)
            self._table.setItem(row, 1, QTableWidgetItem(theme))
        layout.addWidget(self._table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("변경 적용")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def changes(self) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = []
        for row in range(self._table.rowCount()):
            before = self._table.item(row, 0).text().strip()
            after = self._table.item(row, 1).text().strip()
            if before.casefold() != after.casefold():
                values.append((before, after))
        return tuple(values)

class ThemeManagerDialog(QDialog):
    def __init__(self, repository: object, settings: SettingsRepository, on_excel_update: Callable[[], None] | None = None, on_image_update: Callable[[], None] | None = None, on_catalog_sync: Callable[[], None] | None = None, parent: QWidget | None = None, on_themes_changed: Callable[[], None] | None = None) -> None:
        super().__init__(parent); self._repository=repository; self._settings=settings; self._separators=",/|;" + settings.get("theme_custom_separators"); self._on_excel_update=on_excel_update; self._on_image_update=on_image_update; self._on_themes_changed=on_themes_changed; self.setWindowTitle("종목/테마 관리"); self.resize(560,420)
        self._search=QLineEdit(); self._search.setPlaceholderText("종목명 검색"); self._table=QTableWidget(0,2); self._table.setHorizontalHeaderLabels(("종목명","테마"))
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, int(settings.get("theme_manager_stock_column_width")))
        self._table.setColumnWidth(1, int(settings.get("theme_manager_theme_column_width")))
        header.sectionResized.connect(self._save_table_column_width)
        self._custom_separators = QLineEdit(settings.get("theme_custom_separators")); self._custom_separators.setPlaceholderText("기본 , / | ; 외에 사용할 구분 문자")
        self._import_exclusions = QLineEdit(settings.get("theme_import_exclusions")); self._import_exclusions.setPlaceholderText("예: 개별이슈, 단순뉴스")
        self._add=QPushButton("신규 종목 테마 추가"); self._text_import = QPushButton("텍스트 테마 업데이트")
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        stock_tab = QWidget(); stock_layout = QVBoxLayout(stock_tab)
        theme_tab = QWidget(); theme_layout = QVBoxLayout(theme_tab)
        stock_layout.addWidget(SettingsDialog._section_title("등록 종목/테마")); stock_layout.addWidget(self._search); stock_layout.addWidget(self._table)
        if on_catalog_sync is not None:
            last = settings.get("krx_stock_catalog_date")
            sync = QPushButton("상장종목 목록 동기화")
            sync.clicked.connect(on_catalog_sync)
            stock_layout.addWidget(SettingsDialog._section_separator())
            stock_layout.addWidget(SettingsDialog._section_title("상장목록 동기화"))
            stock_layout.addWidget(sync)
            stock_layout.addWidget(QLabel(f"마지막 동기화: {last or '없음'}"))
        stock_layout.addStretch()
        self._add.clicked.connect(self._add_new); self._text_import.clicked.connect(self._import_text)
        theme_layout.addWidget(SettingsDialog._section_title("공통 입력 규칙"))
        theme_layout.addWidget(QLabel("추가 테마 구분자"))
        theme_layout.addWidget(self._custom_separators)
        theme_layout.addWidget(QLabel("이미지/Excel/텍스트 업데이트 제외 테마"))
        theme_layout.addWidget(self._import_exclusions)
        theme_layout.addWidget(SettingsDialog._section_separator())
        theme_layout.addWidget(SettingsDialog._section_title("테마 입력 및 갱신"))
        theme_layout.addWidget(self._add)
        theme_layout.addWidget(self._text_import)
        if self._on_excel_update is not None:
            excel = QPushButton("Excel 테마 업데이트")
            excel.clicked.connect(self._on_excel_update)
            theme_layout.addWidget(excel)
        if self._on_image_update is not None:
            image = QPushButton("이미지 테마 업데이트")
            image.clicked.connect(self._on_image_update)
            theme_layout.addWidget(image)
        theme_layout.addWidget(SettingsDialog._section_separator())
        theme_layout.addWidget(SettingsDialog._section_title("테마 일괄 관리"))
        self._bulk_edit = QPushButton("테마 일괄 수정")
        self._clear_all = QPushButton("전체 테마 초기화")
        theme_layout.addWidget(self._bulk_edit)
        theme_layout.addWidget(self._clear_all)
        theme_layout.addStretch()
        tabs.addTab(theme_tab, "테마")
        tabs.addTab(stock_tab, "종목")
        layout.addWidget(tabs)
        self._bulk_edit.clicked.connect(self._rename_theme)
        self._clear_all.clicked.connect(self._clear_all_themes)
        self._custom_separators.textChanged.connect(lambda _: self._save_custom_separators())
        self._import_exclusions.textChanged.connect(lambda _: self._save_import_exclusions())
        self._search.textChanged.connect(self._reload); self._table.cellDoubleClicked.connect(self._edit); self._rows=(); self._reload()
    def _save_custom_separators(self) -> None:
        value = self._custom_separators.text().strip()
        self._settings.set("theme_custom_separators", value)
        self._separators = ",/|;" + value
    def _save_import_exclusions(self) -> None:
        self._settings.set("theme_import_exclusions", self._import_exclusions.text().strip())
    def _save_table_column_width(self, logical_index: int, _old_width: int, new_width: int) -> None:
        if logical_index == 0:
            self._settings.set("theme_manager_stock_column_width", str(new_width))
        elif logical_index == 1:
            self._settings.set("theme_manager_theme_column_width", str(new_width))
    def _filter_import_exclusions(self, rows: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        excluded = {theme_key(theme) for theme in parse_themes(self._settings.get("theme_import_exclusions"), self._separators)}
        if not excluded:
            return rows
        filtered: list[tuple[str, str]] = []
        for name, value in rows:
            themes = tuple(theme for theme in parse_themes(value, self._separators) if theme_key(theme) not in excluded)
            if themes:
                filtered.append((name, "/".join(themes)))
        return tuple(filtered)
    def _reload(self) -> None:
        self._rows=self._repository.search(self._search.text()); self._table.setRowCount(len(self._rows))
        for index,(_,name,themes) in enumerate(self._rows):
            self._table.setItem(index,0,QTableWidgetItem(name)); self._table.setItem(index,1,QTableWidgetItem(themes))
    def _edit(self, row: int, _: int) -> None:
        code, name, themes = self._rows[row]
        dialog = ThemeEditDialog(name, parse_themes(themes, self._separators), self._separators, self)
        if dialog.exec() and QMessageBox.question(self, "테마 변경 확인", f"{name}\n\n기존: {themes or '-'}\n변경: {', '.join(dialog.themes) or '-'}\n\n저장할까요?") == QMessageBox.StandardButton.Yes:
            self._repository.replace_for_stock(code, dialog.themes)
            self._reload()
            self._notify_themes_changed()
    def _add_new(self) -> None:
        dialog = ImageThemeRowsDialog((), self)
        dialog.setWindowTitle("신규 종목 테마 추가")
        labels = dialog.findChildren(QLabel)
        if labels:
            labels[0].setText("종목명과 테마를 여러 행으로 입력하세요. 행 추가·삭제가 가능하며, 적용 전 기존 테마와 비교합니다.")
        if not dialog.exec():
            return
        self._apply_import_rows(dialog.rows())
        return

    def _import_text(self) -> None:
        dialog = TextThemeImportDialog(self._settings, self)
        if not dialog.exec():
            return
        self._separators = ",/|;" + self._settings.get("theme_custom_separators")
        marker = self._settings.get("theme_text_heading_marker")
        rows = parse_theme_text(dialog.text, self._separators, marker)
        if not rows:
            QMessageBox.information(self, "텍스트 확인", f"{marker} 테마 제목 아래에서 종목명을 찾지 못했습니다. 텍스트 내용을 확인하세요.")
            return
        self._apply_import_rows(rows)

    def _apply_import_rows(self, raw_rows: tuple[tuple[str, str], ...]) -> None:
        valid, errors = validate_theme_rows(self._filter_import_exclusions(raw_rows), self._separators)
        if not valid and not errors:
            QMessageBox.information(self, "입력 확인", "추가할 종목과 테마를 한 행 이상 입력하세요.")
            return
        if errors:
            QMessageBox.warning(self, "입력 확인", "\n".join(errors))
            return
        matched, unmatched = match_theme_rows(valid, self._repository)
        resolved, cancelled = self._resolve_unmatched_new_rows(unmatched)
        if cancelled:
            return
        skipped = len(unmatched) - len(resolved)
        matched = matched + resolved
        unmatched = ()
        if unmatched:
            names = ", ".join(row.name for row in unmatched)
            QMessageBox.warning(self, "종목 매칭 실패", f"종목 DB에서 찾을 수 없습니다.\n{names}")
            return
        changes = preview_theme_changes(matched, self._repository)
        preview = ThemePreviewDialog(changes, skipped, self, frozenset(theme_key(theme) for theme in parse_themes(self._settings.get("theme_import_exclusions"), self._separators)))
        if preview.exec():
            changes = preview.changes(self._separators)
            applied = sum(change.status != "변경 없음" for change in changes)
            for change in changes:
                if change.status != "변경 없음":
                    self._repository.replace_for_stock(change.code, change.after)
            self._reload()
            self._notify_themes_changed()
            QMessageBox.information(self, "테마 업데이트 완료", f"{applied}개 종목의 테마를 적용했습니다.")

    def _resolve_unmatched_new_rows(self, rows: tuple[object, ...]) -> tuple[tuple[MatchedThemeRow, ...], bool]:
        resolved: list[MatchedThemeRow] = []
        for row in rows:
            original_name = str(getattr(row, "name", ""))
            candidate, cancelled = choose_similar_stock(self, self._repository, original_name)
            if candidate:
                code, selected_name = candidate
                resolved.append(MatchedThemeRow(code, selected_name, tuple(getattr(row, "themes", ()))))
            elif cancelled:
                return (), True
        return tuple(resolved), False

    def _edit_theme_color(self) -> None:
        options = self._repository.list_themes()
        if not options:
            QMessageBox.information(self, "테마 색상", "먼저 종목에 테마를 등록하세요.")
            return
        names = [name for name, _ in options]
        name, ok = QInputDialog.getItem(self, "테마 색상", "색상을 바꿀 테마", names, 0, False)
        if not ok:
            return
        dialog = ThemeColorDialog(name, dict(options)[name], self, allow_stock_only=False)
        if dialog.exec():
            self._repository.set_color(name, dialog.color)

    def _delete_themes(self) -> None:
        dialog = ThemeBulkDeleteDialog(tuple(name for name, _ in self._repository.list_themes()), self)
        if not dialog.exec() or not dialog.themes:
            return
        labels = ", ".join(dialog.themes)
        if QMessageBox.question(self, "테마 일괄 삭제", f"선택한 테마를 모든 종목에서 삭제합니다.\n\n{labels}\n\n계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        self._repository.delete_themes(dialog.themes)
        self._reload()
        self._notify_themes_changed()

    def _clear_all_themes(self) -> None:
        if QMessageBox.question(self, "전체 테마 초기화", "모든 종목의 테마와 테마 색상을 삭제합니다.\n이 작업은 되돌릴 수 없습니다.\n\n계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        self._repository.clear_all_themes()
        self._reload()
        self._notify_themes_changed()

    def _rename_theme(self) -> None:
        names = [name for name, _ in self._repository.list_themes()]
        if not names:
            QMessageBox.information(self, "테마 일괄 수정", "수정할 테마가 없습니다.")
            return
        dialog = ThemeBulkEditDialog(tuple(names), self)
        if not dialog.exec() or not dialog.changes:
            return
        preview = "\n".join(f"{before} → {after or '삭제'}" for before, after in dialog.changes)
        if QMessageBox.question(self, "테마 일괄 수정", f"다음 변경을 모든 종목에 적용합니다.\n\n{preview}\n\n계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        for before, after in dialog.changes:
            try:
                if after:
                    self._repository.rename_theme(before, after)
                else:
                    self._repository.delete_themes((before,))
            except ValueError as error:
                QMessageBox.warning(self, "테마 일괄 수정", str(error))
                return
        self._reload()
        self._notify_themes_changed()

    def _notify_themes_changed(self) -> None:
        if self._on_themes_changed is not None:
            self._on_themes_changed()


class NxtMarkerDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: object, index: object) -> None:
        # 기본 파란 호버/선택 배경은 행의 강조색을 덮으므로 제거하고, 대신
        # 마우스가 올라간/선택한 바로 그 셀의 왼쪽에만 가는 표시줄을 그린다.
        active = bool(
            option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        )
        cell_option = QStyleOptionViewItem(option)
        cell_option.state &= ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_HasFocus
        )
        super().paint(painter, cell_option, index)
        if active:
            rect = option.rect
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            # 순위 기준 선택 목록의 표시색과 같은 시스템 강조색을 사용한다.
            painter.setBrush(option.palette.color(QPalette.ColorRole.Highlight))
            painter.drawRoundedRect(rect.left() + 1, rect.center().y() - 7, 2, 14, 1, 1)
            painter.restore()
        if not index.data(Qt.ItemDataRole.UserRole + 2):
            return
        rect = option.rect
        size = min(7, rect.width(), rect.height())
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#C00000"))
        painter.drawPolygon(QPolygon((QPoint(rect.right(), rect.bottom()), QPoint(rect.right() - size, rect.bottom()), QPoint(rect.right(), rect.bottom() - size))))
        painter.restore()

class MainWindow(QMainWindow):
    COLUMNS = (("rank","순위"),("stock","종목"),("themes","테마"),("change_rate","등락률"),("strength_1m","1분강도"),("current_price","현재가"),("trade_value_1m","1분"),("trade_value_5m","5분"),("trade_value_60m","60분"),("trade_value_day","1일"),("strength_5m","5분강도"),("strength_60m","60분강도"),("strength_day","1일강도"),("new_high_price","신고가"),("high_distance","신고가%"),("market_cap","시가총액"))
    HEADERS = tuple(label for _, label in COLUMNS)

    def __init__(
        self,
        settings: SettingsRepository,
        ranking_loader: RankingLoader | None = None,
        realtime_worker_factory: Callable[[tuple[str, ...]], RealtimeTradeWorker] | None = None,
        minute_history_worker_factory: Callable[[tuple[str, ...]], MinuteHistoryWorker] | None = None,
        fundamentals_worker_factory: Callable[[tuple[str, ...]], FundamentalsWorker] | None = None,
        minute_aggregator: MinuteTradeValueAggregator | None = None,
        themes: dict[str, str] | None = None,
        columns: ColumnSettingsRepository | None = None,
        stock_lookup: object | None = None,
        theme_store: object | None = None,
        daily_high_worker_factory: Callable[[tuple[str, ...]], DailyHighWorker] | None = None,
        nxt_eligibility_worker_factory: Callable[[tuple[str, ...]], NxtEligibilityWorker] | None = None,
        google_drive_sync: GoogleDriveSyncService | None = None,
        initial_google_drive_download: bool = False,
        api_runtime_factory: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._ranking_loader = ranking_loader
        self._realtime_worker_factory = realtime_worker_factory
        self._minute_history_worker_factory = minute_history_worker_factory
        self._fundamentals_worker_factory = fundamentals_worker_factory
        self._daily_high_worker_factory = daily_high_worker_factory
        self._nxt_eligibility_worker_factory = nxt_eligibility_worker_factory
        self._fundamentals: dict[str, StockFundamentals] = {}
        self._daily_highs: dict[str, DailyHighTargets] = {}
        self._previous_day_trade_values: dict[str, float] = {}
        self._themes = themes or {}
        self._pending_price_cache: dict[str, int] = {}
        self._columns = columns
        self._stock_lookup = stock_lookup
        self._theme_store = theme_store
        self._google_drive_sync = google_drive_sync
        self._api_runtime_factory = api_runtime_factory
        self._api_reloading = False
        self._google_drive_worker: GoogleDriveSyncWorker | None = None
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_download_worker: UpdateDownloadWorker | None = None
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
        self._realtime_worker: RealtimeTradeWorker | None = None
        self._realtime_codes: tuple[str, ...] = ()
        self._minute_history_worker: MinuteHistoryWorker | None = None
        self._fundamentals_worker: FundamentalsWorker | None = None
        self._daily_high_worker: DailyHighWorker | None = None
        self._nxt_eligibility_worker: NxtEligibilityWorker | None = None
        self._new_high_worker: NewHighWorker | None = None
        self._ranking_worker: RankingWorker | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._deferred_ranking_stocks: tuple[object, ...] | None = None
        self._deferred_ranking_flush_scheduled = False
        self._table_update_deferred = False
        self._table_update_flush_scheduled = False
        self._partial_ranking_retry_count = 0
        self._initial_ranking_size_adjusted = False
        self._ranking_request_due = False
        self._last_ranking_signature: tuple[tuple[object, object, object], ...] = ()
        self._last_rank_by_code: dict[str, int] = {}
        self._rank_changed_codes: set[str] = set()
        self._selected_table_cell: tuple[int, int] | None = None
        self._ranking_priority_preparing = False
        self._resizing_columns = False
        self._restoring_columns = False
        self._column_auto_fit_ready = False
        self._manual_column_resize_until = 0.0
        self._initial_new_high_refresh_started = False
        self._initial_nxt_codes: tuple[str, ...] = ()
        self._closing = False
        self._row_by_code: dict[str, int] = {}
        self._ranked_stock_names: dict[str, str] = {}
        self._current_prices: dict[str, int] = {}
        self._today_high_codes: set[str] = set()
        self._today_high_prices: dict[str, int] = {}
        self._near_high_codes: set[str] = set()
        self._near_high_levels: dict[str, str] = {}
        self._nxt_checked_codes: set[str] = set()
        self._nxt_enabled_codes: set[str] = set()
        self._near_high_sound_players: dict[str, tuple[QMediaPlayer, QAudioOutput]] = {}
        self._new_high_periods: dict[str, frozenset[int]] = {}
        self._minute_history_codes: set[str] = set()
        self._minute_aggregator = minute_aggregator or MinuteTradeValueAggregator()
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
        self._window_geometry_save_timer = QTimer(self)
        self._window_geometry_save_timer.setSingleShot(True)
        self._window_geometry_save_timer.setInterval(350)
        self._window_geometry_save_timer.timeout.connect(self._save_window_geometry)
        self.setWindowTitle("키움 실시간 종목 모니터")
        self.resize(int(self._settings.get("window_width")), int(self._settings.get("window_height")))
        self.setMinimumWidth(320)

        toolbar = QToolBar("도구")
        toolbar.setObjectName("main_tools_toolbar")
        toolbar.setMovable(False)
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._refresh_button = QPushButton("새로고침")
        self._refresh_button.clicked.connect(self._refresh_rankings)
        self._refresh_button.setEnabled(ranking_loader is not None)
        toolbar.addWidget(self._refresh_button)
        self._new_high_button = QPushButton("신고가 새로고침")
        self._new_high_button.clicked.connect(self._refresh_new_highs)
        self._new_high_button.setEnabled(ranking_loader is not None)
        toolbar.addWidget(self._new_high_button)
        settings_button = QPushButton("기본 설정")
        settings_button.clicked.connect(self._open_settings)
        toolbar.addWidget(settings_button)
        version_label = QLabel(f"버전 {APP_VERSION}")
        version_label.setStyleSheet("color: #667085; padding-left: 6px;")
        toolbar.addWidget(version_label)
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
        self._environment_selector = QComboBox()
        self._environment_selector.addItem("모의투자", "mock")
        self._environment_selector.addItem("실전투자", "real")
        self._restore_environment_selector()
        self._environment_selector.currentIndexChanged.connect(self._change_environment)
        toolbar.addWidget(self._environment_selector)
        self.addToolBar(toolbar)

        self._table = QTableWidget(1, len(self.HEADERS))
        self._table.setHorizontalHeaderLabels(self.HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self._table.cellClicked.connect(self._toggle_table_cell_selection)
        self._table.cellDoubleClicked.connect(self._edit_theme_from_main_table)
        self._table.setItemDelegate(NxtMarkerDelegate(self._table))
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionsMovable(True)
        self._table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.horizontalHeader().customContextMenuRequested.connect(self._show_column_menu)
        self._table.horizontalHeader().sectionMoved.connect(lambda *_: self._save_columns())
        self._table.horizontalHeader().sectionResized.connect(self._on_column_resized)
        self._table.horizontalHeader().sectionClicked.connect(self._toggle_trade_display_mode)
        # 표의 오른쪽/아래 빈 공간을 더블 클릭하면 창을 표 크기에 맞춘다.
        self._table.viewport().installEventFilter(self)
        self._update_trade_display_headers()
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
        message = "새로고침으로 키움 REST 조회를 시작합니다." if ranking_loader else "상단 API 설정에서 키를 입력해 연결할 수 있습니다."
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
        )
        # 기본 설정은 메인 표를 막지 않는 별도 창으로 연다. 따라서 순위 갱신은
        # 설정 창이 열려 있어도 즉시 표에 반영된다.
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.finished.connect(lambda result, source=dialog: self._on_settings_closed(source, result))
        self._settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _check_for_updates(self) -> None:
        if self._update_check_worker is not None and self._update_check_worker.isRunning():
            self.statusBar().showMessage("업데이트 정보를 확인하고 있습니다…")
            return
        worker = UpdateCheckWorker(self)
        self._update_check_worker = worker
        worker.completed.connect(self._on_update_check_completed)
        worker.failed.connect(self._on_update_check_failed)
        worker.finished.connect(lambda: setattr(self, "_update_check_worker", None))
        self.statusBar().showMessage("업데이트 정보를 확인하고 있습니다…")
        worker.start()

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, ...]:
        try:
            return tuple(int(part) for part in value.split("."))
        except ValueError:
            return ()

    def _on_update_check_completed(self, version: str, update_url: str, release_url: str) -> None:
        if self._version_tuple(version) <= self._version_tuple(APP_VERSION):
            self.statusBar().showMessage("현재 최신 버전을 사용하고 있습니다.", 4_000)
            QMessageBox.information(self, "앱 업데이트", f"현재 최신 버전입니다.\n\n현재 버전: {APP_VERSION}")
            return
        if not update_url:
            answer = QMessageBox.question(
                self,
                "앱 업데이트",
                f"새 버전 {version}이 있습니다.\n현재 버전: {APP_VERSION}\n\n부분 업데이트 파일이 아직 준비되지 않았습니다. 릴리즈 페이지를 열까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(release_url))
            return
        if not getattr(sys, "frozen", False):
            QMessageBox.information(self, "앱 업데이트", "개발 실행 중에는 자동 업데이트를 적용하지 않습니다.\n설치본에서 자동 업데이트를 사용할 수 있습니다.")
            return
        answer = QMessageBox.question(
            self,
            "앱 업데이트",
            f"새 버전 {version}이 있습니다.\n현재 버전: {APP_VERSION}\n\n변경된 파일만 자동으로 다운로드해 설치할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._download_update(update_url, version)

    def _download_update(self, url: str, version: str) -> None:
        worker = UpdateDownloadWorker(url, version, self)
        self._update_download_worker = worker
        progress = QProgressDialog("업데이트 파일을 다운로드하고 있습니다…", "취소", 0, 100, self)
        progress.setWindowTitle("앱 업데이트")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._update_progress_dialog = progress
        progress.canceled.connect(worker.requestInterruption)
        worker.progress.connect(progress.setValue)
        worker.completed.connect(self._apply_downloaded_update)
        worker.failed.connect(self._on_update_download_failed)
        worker.finished.connect(self._close_update_progress)
        self.statusBar().showMessage("업데이트 파일을 다운로드하고 있습니다…")
        progress.show()
        worker.start()

    def _close_update_progress(self) -> None:
        if getattr(self, "_update_progress_dialog", None) is not None:
            self._update_progress_dialog.close()
            self._update_progress_dialog = None
        self._update_download_worker = None

    def _apply_downloaded_update(self, archive_path: str) -> None:
        app_root = Path(sys.executable).resolve().parent
        script = AppPaths.for_current_user().data_dir.parent / "updates" / "apply_update.ps1"
        escaped_archive = archive_path.replace("'", "''")
        escaped_root = str(app_root).replace("'", "''")
        escaped_exe = str(app_root / Path(sys.executable).name).replace("'", "''")
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"$processId = {os.getpid()}\n"
            f"$archive = '{escaped_archive}'\n$target = '{escaped_root}'\n$exe = '{escaped_exe}'\n"
            "$staging = Join-Path (Split-Path $archive) 'staging'\n"
            "Wait-Process -Id $processId -ErrorAction SilentlyContinue\n"
            "Add-Type -AssemblyName PresentationFramework; Add-Type -AssemblyName System.Windows.Forms\n"
            "$window = New-Object Windows.Window; $window.Title = '키움 실시간 모니터 업데이트'; $window.Width = 420; $window.Height = 145; $window.ResizeMode = 'NoResize'; $window.WindowStartupLocation = 'CenterScreen'\n"
            "$panel = New-Object Windows.Controls.StackPanel; $panel.Margin = '22'; $status = New-Object Windows.Controls.TextBlock; $status.Text = '업데이트를 적용하고 있습니다…'; $bar = New-Object Windows.Controls.ProgressBar; $bar.Height = 20; $bar.Margin = '0,14,0,0'; $bar.Minimum = 0; $bar.Maximum = 100; $panel.Children.Add($status) | Out-Null; $panel.Children.Add($bar) | Out-Null; $window.Content = $panel; $window.Show() | Out-Null\n"
            "Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue\n"
            "Expand-Archive -Path $archive -DestinationPath $staging -Force\n"
            "$manifest = Get-Content (Join-Path $staging 'update_manifest.json') -Raw | ConvertFrom-Json\n"
            "$total = ($manifest.changed | ForEach-Object { (Get-Item (Join-Path $staging $_)).Length } | Measure-Object -Sum).Sum; $done = 0\n"
            "foreach ($file in $manifest.changed) { $source = Join-Path $staging $file; $destination = Join-Path $target $file; $status.Text = '파일을 교체하고 있습니다…'; New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null; Copy-Item $source $destination -Force; $done += (Get-Item $source).Length; $bar.Value = [Math]::Min(100, [Math]::Round(100 * $done / [Math]::Max(1,$total))); [Windows.Forms.Application]::DoEvents() }\n"
            "foreach ($file in $manifest.deleted) { Remove-Item (Join-Path $target $file) -Force -ErrorAction SilentlyContinue }\n"
            "Remove-Item $archive -Force -ErrorAction SilentlyContinue\n"
            "Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue\n"
            "Start-Process -FilePath $exe\n"
            "$window.Close()\n"
            "Remove-Item $PSCommandPath -Force -ErrorAction SilentlyContinue\n",
            encoding="utf-8",
        )
        try:
            arguments = f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}"'
            result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", arguments, None, 0)
            if result <= 32:
                raise OSError(f"Windows 권한 확인이 취소되었거나 시작에 실패했습니다. ({result})")
        except OSError as error:
            QMessageBox.critical(self, "앱 업데이트", f"업데이트 설치를 시작하지 못했습니다.\n{error}")
            return
        self.statusBar().showMessage("업데이트 설치를 시작합니다. Windows 권한 확인 후 앱이 다시 열립니다.")
        QTimer.singleShot(300, self.close)

    def _on_update_download_failed(self, message: str) -> None:
        logger.warning("업데이트 다운로드 실패: %s", message)
        QMessageBox.information(self, "앱 업데이트", "업데이트 파일을 내려받지 못했습니다. 잠시 후 다시 시도하세요.")

    def _on_update_check_failed(self, message: str) -> None:
        logger.info("업데이트 확인 실패: %s", message)
        self.statusBar().showMessage("업데이트 정보를 확인하지 못했습니다.", 5_000)
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
        self.statusBar().showMessage("기본 설정 저장 완료")
        self._schedule_google_drive_upload()
        if dialog.api_changed:
            self._restart_for_api_settings()

    def _on_themes_changed(self) -> None:
        if self._theme_store is not None:
            self._themes = self._theme_store.all_by_name()
        self._refresh_rankings()
        self._schedule_google_drive_upload()

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
        self._start_google_drive_sync("download", interactive=True, allow_connect=True)

    def _disconnect_google_drive(self) -> None:
        if self._google_drive_sync is None:
            return
        self._google_drive_sync.disconnect()
        self._refresh_google_drive_status()
        self.statusBar().showMessage("Google Drive 계정 연결을 해제했습니다. 기존 Drive 파일은 그대로 유지됩니다.")

    def _schedule_google_drive_upload(self) -> None:
        if self._google_drive_sync is not None and self._google_drive_sync.connected and self._settings.get("google_drive_auto_upload") == "1" and not self._closing:
            self._google_drive_dirty = True
            self._settings.set("google_drive_unsynced_changes", "1")
            self._settings.set("google_drive_local_changed_at", self._google_drive_timestamp_now())
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
        if self._google_drive_worker is not None and self._google_drive_worker.isRunning():
            if close_after:
                self._google_drive_close_pending = True
            return
        worker = GoogleDriveSyncWorker(service, operation, self._settings.get("google_drive_sync_target"), interactive, self)
        self._google_drive_worker = worker
        self._google_drive_operation = operation
        self._google_drive_show_completion = notify_on_success
        if operation == "metadata":
            worker.metadata_received.connect(self._on_google_drive_metadata_received)
        else:
            worker.completed.connect(self._on_google_drive_sync_completed)
        worker.failed.connect(self._on_google_drive_sync_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()
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
        self._google_drive_worker = None
        self._google_drive_operation = ""
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
        self._google_drive_worker = None
        self._google_drive_operation = ""
        self._google_drive_show_completion = False
        if operation == "upload":
            self._google_drive_dirty = False
            self._settings.set("google_drive_unsynced_changes", "0")
            self._settings.set("google_drive_last_upload_success_at", self._google_drive_timestamp_now())
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
        self._google_drive_first_backup_pending = False
        self._google_drive_worker = None
        self._google_drive_operation = ""
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
        """같은 셀을 다시 누르면 선택 표시를 해제한다."""
        cell = (row, column)
        if self._selected_table_cell == cell:
            self._table.clearSelection()
            self._table.setCurrentItem(None)
            self._selected_table_cell = None
        else:
            self._selected_table_cell = cell
        self._table.viewport().update()

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

    def _open_log_file(self) -> None:
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
        log_dir = root / "data" / "logs"
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
            SettingsBackupService(self._settings.database_path).export_to(target)
        except OSError as error:
            QMessageBox.warning(self, "설정 백업", f"설정 백업을 저장하지 못했습니다.\n{error}")
            return
        QMessageBox.information(self, "설정 백업", "설정·표 구성·종목/테마 정보를 저장했습니다.\nAPI 키, 로그, 실시간·분봉 데이터는 포함하지 않았습니다.")

    def _import_settings_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "설정 백업 불러오기", "", "설정 백업 (*.json)")
        if not path:
            return
        if QMessageBox.question(
            self,
            "설정 복원",
            "현재 설정·표 구성·종목/테마 정보를 백업 파일 내용으로 바꿉니다. 계속할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            SettingsBackupService(self._settings.database_path).import_from(Path(path))
        except SettingsBackupError as error:
            QMessageBox.warning(self, "설정 복원", str(error))
            return
        QMessageBox.information(self, "설정 복원", "설정을 복원했습니다. 프로그램을 다시 시작하면 적용됩니다.")

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
        QMessageBox.information(self, "테마 DB 저장", "테마·종목 연결·테마 색·별칭·등록 종목을 저장했습니다.\n일반 설정, 필드 구성, API 키, 로그는 포함하지 않았습니다.")

    def _import_theme_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "테마 DB 불러오기", "", "테마 DB 백업 (*.json)")
        if not path:
            return
        if QMessageBox.question(self, "테마 DB 불러오기", "현재 테마·종목 연결·테마 색·별칭을 백업 내용으로 바꿉니다. 일반 설정은 바뀌지 않습니다. 계속할까요?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            ThemeBackupService(self._settings.database_path).import_from(Path(path))
        except ThemeBackupError as error:
            QMessageBox.warning(self, "테마 DB 불러오기", str(error))
            return
        self._on_themes_changed()
        QMessageBox.information(self, "테마 DB 불러오기", "테마 DB를 불러왔습니다.")

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
            self._themes = self._theme_store.all_by_name()
            self._refresh_rankings()

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
        path = self._api_config_path()
        dialog = ApiSettingsDialog(path, self)
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
        self._ranking_request_due = False
        # 기존 API 응답이 새 설정의 화면을 다시 덮어쓰지 않도록, 작업 정리
        # 중에는 후속 보완 조회 연결도 잠시 보류한다.
        self._ranking_priority_preparing = True
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
            self._minute_history_worker_factory = runtime.get("minute_history_worker_factory")  # type: ignore[assignment]
            self._fundamentals_worker_factory = runtime.get("fundamentals_worker_factory")  # type: ignore[assignment]
            self._daily_high_worker_factory = runtime.get("daily_high_worker_factory")  # type: ignore[assignment]
            self._nxt_eligibility_worker_factory = runtime.get("nxt_eligibility_worker_factory")  # type: ignore[assignment]
        except Exception as error:
            self._api_reloading = False
            self._ranking_priority_preparing = False
            self._set_api_status("API: 오류", "#C00000")
            QMessageBox.warning(self, "API 설정", f"새 API 설정을 적용하지 못했습니다.\n{error}")
            return

        self._realtime_codes = ()
        self._minute_history_codes.clear()
        self._minute_aggregator = MinuteTradeValueAggregator()
        self._initial_new_high_refresh_started = False
        self._initial_nxt_codes = ()
        self._api_reloading = False
        self._ranking_priority_preparing = False
        self._restore_environment_selector()
        self.statusBar().showMessage("새 API 설정 적용 완료 · 순위를 다시 조회합니다.")
        self._start_initial_ranking()

    def _select_theme_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "테마 이미지 선택", self._settings.get("theme_image_import_dir"), "이미지 파일 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        self._settings.set("theme_image_import_dir", str(Path(path).parent))
        self._start_image_theme_ocr(Path(path))

    def _start_image_theme_ocr(self, image_path: Path) -> None:
        worker = ImageThemeOcrWorker(image_path)
        worker.setParent(self)
        worker.completed.connect(self._show_image_theme_rows)
        worker.failed.connect(lambda message: QMessageBox.warning(self, "이미지 OCR 실패", message))
        worker.finished.connect(lambda: self.statusBar().showMessage("이미지 OCR 작업 종료"))
        self._image_theme_ocr_worker = worker
        self.statusBar().showMessage("이미지 OCR 모델을 준비하고 있습니다. 첫 실행은 모델 다운로드로 시간이 걸릴 수 있습니다.")
        worker.start()
        QTimer.singleShot(60_000, lambda: self._warn_slow_image_ocr(worker))

    def _with_krx_stock_catalog(self, continuation: Callable[[], None]) -> None:
        if self._stock_lookup is None or not hasattr(self._stock_lookup, "upsert_many"):
            continuation(); return
        worker = KrxStockCatalogWorker(self._stock_lookup, self._settings)
        worker.setParent(self)
        def completed(count: int, cached: bool) -> None:
            self.statusBar().showMessage("KRX 상장종목 목록 확인 완료" if cached else f"KRX 상장종목 {count:,}개 갱신 완료")
            continuation()
        worker.completed.connect(completed)
        worker.failed.connect(lambda message: (QMessageBox.warning(self, "KRX 상장종목 목록", f"전체 목록을 갱신하지 못했습니다.\n저장된 목록으로 계속합니다.\n\n{message}"), continuation()))
        self._krx_stock_catalog_worker = worker
        self.statusBar().showMessage("KRX 전체 상장종목 목록을 확인하고 있습니다.")
        worker.start()

    def _sync_krx_stock_catalog(self) -> None:
        if self._stock_lookup is None or not hasattr(self._stock_lookup, "upsert_many"):
            return
        worker = KrxStockCatalogWorker(self._stock_lookup, self._settings)
        worker.setParent(self)
        worker.completed.connect(lambda count, cached: QMessageBox.information(self, "상장종목 목록 동기화", f"동기화 완료\n성공 날짜: {self._settings.get('krx_stock_catalog_date')}\n{'오늘 이미 받은 목록입니다.' if cached else f'{count:,}개 종목을 갱신했습니다.'}"))
        worker.failed.connect(lambda message: QMessageBox.warning(self, "상장종목 목록 동기화 실패", message))
        self._krx_stock_catalog_worker = worker
        self.statusBar().showMessage("KRX 전체 상장종목 목록을 동기화하고 있습니다.")
        worker.start()

    def _start_daily_krx_catalog_sync(self) -> None:
        """상장종목 목록은 하루 한 번, 모든 주식 API 보완 뒤에만 갱신한다."""
        if self._closing or self._ranking_priority_preparing or self._stock_lookup is None or not hasattr(self._stock_lookup, "upsert_many"):
            return
        if getattr(self, "_krx_stock_catalog_worker", None) is not None and self._krx_stock_catalog_worker.isRunning():
            return
        today = self._ranking_now().strftime("%Y-%m-%d")
        if self._settings.get("krx_stock_catalog_date").startswith(today):
            return
        worker = KrxStockCatalogWorker(self._stock_lookup, self._settings)
        worker.setParent(self)
        worker.completed.connect(lambda count, _cached: self.statusBar().showMessage(f"KRX 상장종목 {count:,}개 자동 동기화 완료"))
        worker.failed.connect(lambda message: logger.warning("KRX 상장종목 자동 동기화 실패: %s", message))
        self._krx_stock_catalog_worker = worker
        self.statusBar().showMessage("최하위 작업: KRX 전체 상장종목 목록 동기화 중")
        worker.start()

    def _warn_slow_image_ocr(self, worker: ImageThemeOcrWorker) -> None:
        if worker is not getattr(self, "_image_theme_ocr_worker", None) or not worker.isRunning():
            return
        self.statusBar().showMessage("OCR 모델 다운로드가 1분 이상 걸리고 있습니다. 네트워크 연결을 확인하거나 취소할 수 있습니다.")
        if QMessageBox.question(self, "OCR 다운로드 지연", "한국어 OCR 모델 다운로드가 1분 이상 걸리고 있습니다.\n\n계속 기다릴까요?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.No:
            worker.requestInterruption()

    def _show_image_theme_rows(self, rows: object) -> None:
        if not isinstance(rows, tuple):
            return
        dialog = ImageThemeRowsDialog(rows, self)
        if dialog.exec():
            separators = ",/|;" + self._settings.get("theme_custom_separators")
            imported, errors = validate_theme_rows(self._filter_import_exclusions(dialog.rows(), separators), separators)
            if errors:
                QMessageBox.warning(self, "이미지 테마 확인", "\n".join(errors))
                return
            matched, unmatched = match_theme_rows(imported, self._stock_lookup) if self._stock_lookup else ((), imported)
            resolved, cancelled = self._resolve_unmatched_theme_rows(unmatched, "이미지 OCR")
            if cancelled:
                return
            changes = preview_theme_changes(matched + resolved, self._theme_store) if self._theme_store else ()
            preview = ThemePreviewDialog(changes, len(unmatched) - len(resolved), self, frozenset(theme_key(theme) for theme in parse_themes(self._settings.get("theme_import_exclusions"), separators)))
            if preview.exec() and self._theme_store:
                changes = preview.changes(separators)
                applied = sum(change.status != "변경 없음" for change in changes)
                for change in changes:
                    if change.status != "변경 없음":
                        self._theme_store.replace_for_stock(change.code, change.after)
                self._themes = self._theme_store.all_by_name()
                self._refresh_rankings()
                QMessageBox.information(self, "이미지 테마 업데이트 완료", f"{applied}개 종목의 테마를 적용했습니다.")
            self.statusBar().showMessage(f"이미지 테마 결과 · {len(changes)}개 확인 · 적용은 최종 확인 후에만 수행됩니다")

    def _select_excel(self) -> None:
        self._choose_excel_after_catalog()

    def _choose_excel_after_catalog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "테마 Excel 선택", self._settings.get("theme_excel_import_dir"), "Excel 파일 (*.xlsx)")
        if path:
            self._settings.set("theme_excel_import_dir", str(Path(path).parent))
            try:
                source = ExcelThemeRepository(Path(path)); header, raw_rows = source.load_header_and_rows()
                separators = ",/|;" + self._settings.get("theme_custom_separators")
                rows, errors = validate_theme_rows(self._filter_import_exclusions(raw_rows, separators), separators)
                errors = validate_theme_header(header) + errors
            except Exception as error:
                self.statusBar().showMessage(f"Excel 읽기 실패: {error}")
                return
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
                preview = ThemePreviewDialog(changes, len(unmatched), self, frozenset(theme_key(theme) for theme in parse_themes(self._settings.get("theme_import_exclusions"), separators)))
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

    def _filter_import_exclusions(self, rows: tuple[tuple[str, str], ...], separators: str) -> tuple[tuple[str, str], ...]:
        excluded = {theme_key(theme) for theme in parse_themes(self._settings.get("theme_import_exclusions"), separators)}
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
            while True:
                name, ok = QInputDialog.getText(
                    self,
                    "키움 종목명 확인",
                    f"{source_label}에서 읽은 '{original_name}' 종목명이 현재 키움 종목 목록에 없습니다.\n"
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
                candidate, cancelled = choose_similar_stock(self, self._stock_lookup, name)
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
        if self._columns is None:
            return False
        saved = {s.name: s for s in self._columns.list()}
        header = self._table.horizontalHeader()
        before = tuple((not self._table.isColumnHidden(logical), header.visualIndex(logical)) for logical, _ in enumerate(self.COLUMNS))
        self._restoring_columns = True
        try:
            for logical, (name, _) in enumerate(self.COLUMNS):
                setting = saved.get(name)
                if setting:
                    self._table.setColumnHidden(logical, not setting.visible); header.resizeSection(logical, setting.width)
            names = [name for name, _ in self.COLUMNS]
            for target, setting in enumerate(sorted(saved.values(), key=lambda s: s.position)):
                if setting.name in names:
                    logical = names.index(setting.name)
                    header.moveSection(header.visualIndex(logical), target)
        finally:
            self._restoring_columns = False
        after = tuple((not self._table.isColumnHidden(logical), header.visualIndex(logical)) for logical, _ in enumerate(self.COLUMNS))
        return before != after

    def _save_columns(self) -> None:
        if self._columns is None: return
        header = self._table.horizontalHeader(); settings=[]
        for logical,(name,_) in enumerate(self.COLUMNS): settings.append(ColumnSetting(name, not self._table.isColumnHidden(logical), header.visualIndex(logical), header.sectionSize(logical)))
        self._columns.save(tuple(settings))

    def _on_column_resized(self, *_: object) -> None:
        if not self._resizing_columns and not self._restoring_columns:
            self._manual_column_resize_until = time.monotonic() + 30.0
            self._save_columns()

    def _resize_columns_proportionally(self) -> None:
        if not self._column_auto_fit_ready or self._settings.get("ui_mode") != "responsive" or time.monotonic() < self._manual_column_resize_until or not hasattr(self, "_table"):
            return
        table = self._table
        visible = [index for index in range(table.columnCount()) if not table.isColumnHidden(index)]
        if not visible:
            return
        available = table.viewport().width()
        current_total = sum(table.columnWidth(index) for index in visible)
        if available <= 0 or current_total <= 0 or available == current_total:
            return
        self._resizing_columns = True
        try:
            widths = [max(40, round(table.columnWidth(index) * available / current_total)) for index in visible]
            difference = available - sum(widths)
            widths[-1] = max(40, widths[-1] + difference)
            for index, width in zip(visible, widths):
                table.setColumnWidth(index, width)
        finally:
            self._resizing_columns = False

    def _enable_initial_column_auto_fit(self) -> None:
        """초기 복원 완료 뒤부터 실제 창 크기 변경에만 자동 맞춤을 허용한다."""
        self._column_auto_fit_ready = True

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
        self._table.setColumnHidden(index, not visible)
        self._table.updateGeometry()
        self._table.horizontalHeader().viewport().update()
        self._resize_columns_proportionally()
        QTimer.singleShot(0, self._resize_columns_proportionally)
        self._save_columns()

    def _enable_column_auto_fit(self) -> None:
        self._manual_column_resize_until = 0.0
        self._resizing_columns = True
        try:
            self._table.resizeColumnsToContents()
        finally:
            self._resizing_columns = False
        self._resize_columns_proportionally()
        self._save_columns()

    def _reset_columns(self) -> None:
        if self._columns is None:
            return
        self._columns.reset()
        self._manual_column_resize_until = 0.0
        self._restore_columns()
        self.statusBar().showMessage("컬럼 표시, 순서, 폭을 기본값으로 초기화했습니다.")

    def _on_ranking_timer(self) -> None:
        # 순위 기준 시각에는 반드시 이 요청을 먼저 시작한다. 직전 준비 단계에서
        # 보완 조회를 멈춰 두었기 때문에 REST 연결 대기 가능성을 최소화한다.
        self._ranking_priority_preparing = False
        now = self._ranking_now()
        if self._ranking_worker is not None and self._ranking_worker.isRunning():
            # 기준 시각에 겹친 요청을 없애지 않는다. 기존 요청이 끝나는 즉시
            # 한 번 더 조회해 순위 갱신 회차가 빠지는 일을 막는다.
            self._ranking_request_due = True
            logger.warning("순위 기준 시각 %s: 이전 순위 조회가 진행 중이라 완료 직후 재조회합니다.", now.strftime("%H:%M:%S"))
            return
        logger.info("순위 기준 시각 %s: 순위 조회를 시작합니다.", now.strftime("%H:%M:%S"))
        self._refresh_rankings()

    def _prepare_ranking_refresh(self) -> None:
        """순위 기준 시각 직전에 저우선순위 REST 보완 요청을 양보시킨다."""
        if self._closing:
            return
        self._ranking_priority_preparing = True
        for worker in (
            self._minute_history_worker,
            self._daily_high_worker,
            self._fundamentals_worker,
            self._nxt_eligibility_worker,
            getattr(self, "_krx_stock_catalog_worker", None),
        ):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()

    def _schedule_next_ranking_refresh(self) -> None:
        if self._closing or self._ranking_loader is None:
            return
        now = self._ranking_now()
        query_type = self._settings.get("rank_query_type")
        if query_type == "5":
            base = now.replace(microsecond=0)
            next_time = base.replace(second=30) if base.second < 30 else (base + timedelta(minutes=1)).replace(second=0)
        elif query_type == "1":
            next_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        elif query_type == "2":
            base = now.replace(second=0, microsecond=0)
            next_time = base + timedelta(minutes=10 - (base.minute % 10))
        elif query_type == "3":
            next_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        else:
            base = now.replace(microsecond=0)
            next_time = base.replace(second=30) if base.second < 30 else (base + timedelta(minutes=1)).replace(second=0)
        # 키움 API 시간은 그대로 사용한다. 순위 응답의 스냅샷 반영 시점만
        # 사용자가 설정한 값으로 미세 조정한다.
        request_time = next_time + timedelta(milliseconds=250)
        delay_ms = max(100, round((request_time - now).total_seconds() * 1000))
        self._ranking_timer.start(delay_ms)
        # 현재 진행 중인 HTTP 요청은 강제로 끊지 않는다. 다음 보완 요청만
        # 막을 수 있도록 기준 시각 2.5초 전에 준비를 시작한다.
        self._ranking_preparation_timer.start(max(100, delay_ms - 2_500))
        logger.info(
            "다음 순위 조회 예약: %s + 0.25초 (기준 %s)",
            next_time.strftime("%H:%M:%S"),
            query_type,
        )

    def _ranking_now(self) -> datetime:
        provider = getattr(self._ranking_loader, "server_now", None)
        value = provider() if callable(provider) else None
        return value if isinstance(value, datetime) else datetime.now()

    def _update_clock_label(self) -> None:
        visible = self._settings.get("show_server_clock") == "1"
        self._clock_label.setVisible(visible)
        if visible:
            self._clock_label.setText(self._ranking_now().strftime("%H:%M:%S"))

    def _refresh_rankings(self) -> None:
        if self._closing or self._ranking_loader is None:
            return
        if self._ranking_worker is not None and self._ranking_worker.isRunning():
            return
        self._refresh_button.setEnabled(False)
        self._set_api_status("API: 연결 중…", "#B36B00")
        self.statusBar().showMessage("순위와 신고가를 조회하는 중입니다…")
        worker = RankingWorker(self._ranking_loader)
        worker.setParent(self)
        worker.completed.connect(self._on_ranking_loaded)
        worker.failed.connect(self._on_ranking_failed)
        worker.finished.connect(self._on_ranking_worker_finished)
        self._ranking_worker = worker
        logger.info("순위 조회 작업 시작: %s", self._ranking_now().strftime("%H:%M:%S"))
        worker.start()

    def _on_ranking_worker_finished(self) -> None:
        self._refresh_button.setEnabled(True)
        if self._closing or not self._ranking_request_due:
            return
        self._ranking_request_due = False
        logger.info("밀린 순위 조회를 즉시 시작합니다.")
        QTimer.singleShot(0, self._refresh_rankings)

    def _on_ranking_failed(self, message: str) -> None:
        self._ranking_priority_preparing = False
        logger.warning("순위 조회에 실패했습니다: %s", message)
        self._set_api_status("API: 오류", "#C00000")
        self.statusBar().showMessage("조회에 실패했습니다. 네트워크와 API 설정을 확인하세요.")
        self._schedule_next_ranking_refresh()

    def _on_ranking_loaded(self, stocks: object) -> None:
        if not isinstance(stocks, tuple):
            self._on_ranking_failed("순위 응답 형식이 올바르지 않습니다.")
            return
        expected_count = int(getattr(self._ranking_loader, "EXPECTED_STOCKS", 0))
        if expected_count and len(stocks) < expected_count:
            self._partial_ranking_retry_count += 1
            self._set_api_status("API: 재조회", "#B36B00")
            self.statusBar().showMessage(f"순위 응답이 {len(stocks)}/{expected_count}개입니다. 기존 목록을 유지하고 다시 조회합니다…")
            if self._partial_ranking_retry_count <= 2:
                QTimer.singleShot(1_500, self._refresh_rankings)
            else:
                self._schedule_next_ranking_refresh()
            return
        # 설정·테마·입력 창을 조작하는 중에는 표 전체를 다시 만들지 않는다.
        # 최신 결과 하나만 보관하고 창이 닫힌 뒤 반영해 입력 끊김을 막는다.
        if QApplication.activeModalWidget() is not None:
            self._deferred_ranking_stocks = stocks
            self._set_api_status("API: 연결됨", "#008000")
            self._schedule_next_ranking_refresh()
            self._schedule_deferred_ranking_flush()
            return
        self._partial_ranking_retry_count = 0
        new_rank_by_code = {
            str(getattr(stock, "code", "")): int(getattr(stock, "rank", 0) or 0)
            for stock in stocks
        }
        self._rank_changed_codes = (
            {
                code
                for code, rank in new_rank_by_code.items()
                if code and self._last_rank_by_code.get(code) != rank
            }
            if self._last_rank_by_code
            else set()
        )
        if self._rank_changed_codes:
            changed_names = ", ".join(
                str(getattr(stock, "name", getattr(stock, "code", "")))
                for stock in stocks
                if str(getattr(stock, "code", "")) in self._rank_changed_codes
            )
            logger.info("실제 순위 변동 감지: %s개 · %s", len(self._rank_changed_codes), changed_names)
        self._last_rank_by_code = new_rank_by_code
        signature = tuple((getattr(stock, "rank", None), getattr(stock, "code", None), getattr(stock, "change_rate", None)) for stock in stocks)
        unchanged = bool(self._last_ranking_signature) and signature == self._last_ranking_signature
        self._last_ranking_signature = signature
        logger.info(
            "순위 조회 완료: %s · %s개 · %s",
            self._ranking_now().strftime("%H:%M:%S"),
            len(stocks),
            "이전 순위와 동일" if unchanged else "순위 변동 반영",
        )

        self._table_stack.setCurrentWidget(self._table)
        self._table.setRowCount(len(stocks))
        self._set_api_status("API: 연결됨", "#008000")
        self._row_by_code.clear()
        self._ranked_stock_names.clear()
        theme_frequency: dict[str, int] = {}
        for stock in stocks:
            raw_themes = self._themes.get("".join(stock.name.split()), "")
            for theme in raw_themes.split(","):
                key = theme.strip().casefold()
                if key:
                    theme_frequency[key] = theme_frequency.get(key, 0) + 1
        self._visible_theme_frequency = theme_frequency
        codes = tuple(stock.code for stock in stocks)
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_last_prices"):
            self._current_prices.update(self._stock_lookup.load_last_prices(codes))
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
        self._apply_table_visuals()
        self._ensure_initial_ranking_rows_visible()
        self._start_rank_changed_highlights()
        self.statusBar().showMessage(f"조회 완료 · {len(stocks)}개 종목 · {'순위 변동 없음' if unchanged else '순위 변동 반영'} · 실시간 체결 데이터 연결 중")
        self._schedule_theme_trade_summary()
        self._start_realtime_subscription(codes)
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
        if self._closing or self._deferred_ranking_stocks is None:
            return
        if QApplication.activeModalWidget() is not None:
            self._schedule_deferred_ranking_flush()
            return
        stocks = self._deferred_ranking_stocks
        self._deferred_ranking_stocks = None
        self._on_ranking_loaded(stocks)

    def _defer_table_update_while_modal(self) -> bool:
        """설정/입력 창을 조작하는 동안에는 메인 표 렌더링을 미룬다."""
        if QApplication.activeModalWidget() is None:
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
        if QApplication.activeModalWidget() is not None:
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

    def _theme_badges(self, code: str, name: str) -> QWidget:
        widget = QWidget(); layout = QHBoxLayout(widget); layout.setContentsMargins(2, 2, 2, 2); layout.setSpacing(3)
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
                badge_size = int(self._settings.get("theme_badge_font_size")); padding = int(self._settings.get("theme_badge_padding"))
                font_style = f"font-size:{badge_size}px;" if badge_size else ""
                badge = QPushButton(theme); badge.setStyleSheet(f"background:{color}; color:{text_color(color)}; border-radius:5px; padding:{padding}px {padding + 3}px; {font_style}")
                badge.clicked.connect(lambda _, value=theme, stock_code=code: self._edit_badge_color(stock_code, value))
                layout.addWidget(badge)
        layout.addStretch(); return widget

    def _new_high_label(self, code: str, label: str) -> str:
        period = int(self._settings.get("high_distance_period"))
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
        daily = self._daily_highs.get(code)
        fundamentals = self._fundamentals.get(code)
        period = self._settings.get("high_distance_period")
        if period == "5":
            historical = daily.high_5_price if daily else None
        elif period == "20":
            historical = daily.high_20_price if daily else None
        else:
            historical = fundamentals.high_250_price if fundamentals else None
        if historical is None:
            return None
        today_high = self._today_high_prices.get(code)
        return max(historical, today_high) if today_high is not None else historical

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

    def _toggle_trade_display_mode(self, logical_index: int) -> None:
        periods = {6: ("1m", "1분"), 7: ("5m", "5분"), 8: ("60m", "60분"), 9: ("day", "1일")}
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
        if self._new_high_worker is not None and self._new_high_worker.isRunning():
            return
        self._new_high_button.setEnabled(False)
        self.statusBar().showMessage("신고가 목록을 갱신하는 중입니다…")
        worker = NewHighWorker(self._ranking_loader)
        worker.setParent(self)
        worker.completed.connect(self._on_new_high_refresh_completed)
        worker.failed.connect(self._on_new_high_refresh_failed)
        worker.finished.connect(lambda: self._new_high_button.setEnabled(True))
        self._new_high_worker = worker
        worker.start()

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
        if self._closing or self._realtime_worker_factory is None:
            return
        active_codes = self._active_realtime_codes(codes)
        if not active_codes:
            if self._realtime_worker is not None and self._realtime_worker.isRunning():
                self._realtime_worker.stop()
                self._realtime_codes = ()
                self.statusBar().showMessage("현재 시간에는 수신 가능한 실시간 체결 종목이 없습니다.")
            return
        if self._realtime_worker is not None and self._realtime_worker.isRunning() and active_codes == self._realtime_codes:
            return
        if self._realtime_worker is not None:
            if not self._realtime_worker.stop():
                self._on_background_failure("이전 실시간 연결을 아직 종료하는 중입니다. 잠시 후 다시 시도합니다.")
                return
        worker = self._realtime_worker_factory(active_codes)
        worker.setParent(self)
        worker.trade_received.connect(self._on_trade_tick)
        worker.status_changed.connect(self.statusBar().showMessage)
        worker.connection_failed.connect(self._on_realtime_failure)
        worker.subscription_ready.connect(lambda: self._start_realtime_followups(codes))
        self._realtime_worker = worker
        self._realtime_codes = active_codes
        worker.start()

    def _active_realtime_codes(self, codes: tuple[str, ...]) -> tuple[str, ...]:
        now = self._ranking_now()
        minutes = now.hour * 60 + now.minute
        nxt_open = 7 * 60 + 55 <= minutes < 20 * 60 + 5
        # 0B의 실제 KRX 구간은 09:00~15:30이다. 이 시간에는 모든 종목을,
        # 그 밖의 NXT 준비·거래 구간에는 NXT 가능·미확인 종목만 구독한다.
        krx_open = 9 * 60 <= minutes < 15 * 60 + 30
        if not nxt_open:
            return ()
        if krx_open:
            return codes
        return tuple(
            code
            for code in codes
            if code not in self._nxt_checked_codes or code in self._nxt_enabled_codes
        )

    def _schedule_realtime_session_refresh(self) -> None:
        if self._closing:
            return
        now = self._ranking_now()
        boundaries = ((7, 55), (8, 55), (9, 0), (15, 30), (15, 35), (20, 5))
        candidates = [
            now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            for hour, minute in boundaries
        ]
        next_boundary = next((value for value in candidates if value > now), candidates[0] + timedelta(days=1))
        self._realtime_session_timer.start(max(100, round((next_boundary - now).total_seconds() * 1000)))

    def _on_realtime_session_boundary(self) -> None:
        if self._closing:
            return
        self._start_realtime_subscription(tuple(self._row_by_code))
        self._schedule_realtime_session_refresh()

    def _start_realtime_followups(self, codes: tuple[str, ...]) -> None:
        """실시간 순위 뒤의 저우선순위 보완 조회를 시작한다."""
        if self._closing or self._ranking_priority_preparing:
            return
        self._start_secondary_loading(codes)

    def _on_trade_tick(self, tick: TradeTick) -> None:
        row = self._row_by_code.get(tick.code)
        if row is None or tick.current_price is None:
            return
        self._current_prices[tick.code] = tick.current_price
        self._pending_price_cache[tick.code] = tick.current_price
        if not self._price_cache_timer.isActive():
            self._price_cache_timer.start()
        if tick.high_price and tick.high_price > 0:
            self._today_high_prices[tick.code] = tick.high_price
            if tick.current_price >= tick.high_price:
                self._today_high_codes.add(tick.code)
        observed_at = self._ranking_now()
        self._minute_aggregator.ingest(tick, observed_at)
        if not hasattr(self, "_pending_trade_ticks"):
            self._pending_trade_ticks: dict[str, TradeTick] = {}
            self._trade_tick_flush_timer = QTimer(self)
            self._trade_tick_flush_timer.setSingleShot(True)
            self._trade_tick_flush_timer.setInterval(120)
            self._trade_tick_flush_timer.timeout.connect(self._flush_trade_tick_updates)
        self._pending_trade_ticks[tick.code] = tick
        if not self._trade_tick_flush_timer.isActive():
            self._trade_tick_flush_timer.start()

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
            if row is None or tick.current_price is None:
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
        if not self._pending_price_cache:
            return
        prices = self._pending_price_cache
        self._pending_price_cache = {}
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_last_prices"):
            self._stock_lookup.update_last_prices(prices)

    def _start_secondary_loading(self, codes: tuple[str, ...]) -> None:
        """Start non-realtime API work in the defined priority order."""
        if self._ranking_priority_preparing:
            return
        if self._is_after_hours_data_pause():
            # 20:05~07:55에는 움직이지 않는 분봉·신고가 데이터를 다시
            # 조회하지 않는다. 기본정보 → NXT → 상장종목 동기화만 허용한다.
            if not self._start_fundamentals_loading(codes):
                self._start_nxt_phase(codes)
            return
        if not self._start_minute_history_loading(codes):
            self._start_daily_high_phase(codes)

    def _is_after_hours_data_pause(self) -> bool:
        now = self._ranking_now()
        if now.weekday() >= 5:
            return True
        minutes = now.hour * 60 + now.minute
        # 거래 시작·종료 경계의 시계 차이를 고려해 07:55부터 준비하고,
        # 20:05 이후에만 불필요한 체결·보완 조회를 멈춘다.
        return 20 * 60 + 5 <= minutes or minutes < 7 * 60 + 55

    def _start_daily_high_phase(self, codes: tuple[str, ...]) -> None:
        if self._ranking_priority_preparing:
            return
        if not self._start_daily_high_loading(codes):
            self._start_fundamentals_phase(codes)

    def _start_fundamentals_phase(self, codes: tuple[str, ...]) -> None:
        if self._ranking_priority_preparing:
            return
        if not self._start_fundamentals_loading(codes):
            self._start_initial_new_high_refresh(codes)

    def _start_nxt_phase(self, codes: tuple[str, ...]) -> None:
        if self._ranking_priority_preparing:
            return
        if not self._start_nxt_eligibility_loading(codes):
            self._start_daily_krx_catalog_sync()

    def _start_initial_new_high_refresh(self, codes: tuple[str, ...]) -> None:
        if self._ranking_priority_preparing:
            return
        if self._initial_new_high_refresh_started:
            if self._initial_nxt_codes:
                nxt_codes, self._initial_nxt_codes = self._initial_nxt_codes, ()
                self._start_nxt_phase(nxt_codes)
            return
        self._initial_new_high_refresh_started = True
        self._initial_nxt_codes = codes
        self._start_new_high_refresh()

    def _start_minute_history_loading(self, codes: tuple[str, ...]) -> bool:
        if self._closing or self._ranking_priority_preparing or self._is_after_hours_data_pause() or self._minute_history_worker_factory is None:
            return False
        if self._minute_history_worker is not None and self._minute_history_worker.isRunning():
            return True
        missing_codes = tuple(code for code in codes if code not in self._minute_history_codes)
        if not missing_codes:
            return False
        worker = self._minute_history_worker_factory(missing_codes)
        worker.setParent(self)
        if not isinstance(worker, MinuteHistoryWorker):
            self._on_background_failure("분봉 보강 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요.")
            worker.deleteLater()
            return False
        worker.history_received.connect(self._on_history_received)
        worker.status_changed.connect(self.statusBar().showMessage)
        worker.failed.connect(self._on_background_failure)
        worker.finished.connect(lambda: self._start_daily_high_phase(codes))
        self._minute_history_worker = worker
        worker.start()
        return True

    def _on_history_received(self, code: str, bars: object) -> None:
        if not isinstance(bars, tuple):
            return
        self._minute_aggregator.seed(code, bars, self._ranking_now())
        self._minute_history_codes.add(code)
        if self._defer_table_update_while_modal():
            return
        self._apply_near_high_background(code)
        self._render_trade_values(code)

    def _start_nxt_eligibility_loading(self, codes: tuple[str, ...]) -> bool:
        if self._closing or self._ranking_priority_preparing or self._nxt_eligibility_worker_factory is None:
            return False
        if self._nxt_eligibility_worker is not None and self._nxt_eligibility_worker.isRunning():
            return True
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_nxt_enabled"):
            today = self._ranking_now().strftime("%Y-%m-%d")
            cached = self._stock_lookup.load_nxt_enabled(codes, today)
            missing = tuple(code for code in codes if code not in cached)
        else:
            missing = tuple(code for code in codes if code not in self._nxt_checked_codes)
        if not missing:
            return False
        worker = self._nxt_eligibility_worker_factory(missing)
        worker.setParent(self)
        worker.received.connect(self._on_nxt_eligibility_received)
        worker.failed.connect(self._on_background_failure)
        worker.finished.connect(self._start_daily_krx_catalog_sync)
        worker.finished.connect(lambda: self._start_realtime_subscription(tuple(self._row_by_code)))
        self._nxt_eligibility_worker = worker
        worker.start()
        return True

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
            self._play_near_high_sound(level)

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

    def _start_daily_high_loading(self, codes: tuple[str, ...]) -> bool:
        if self._closing or self._ranking_priority_preparing or self._daily_high_worker_factory is None or (self._daily_high_worker is not None and self._daily_high_worker.isRunning()):
            return self._daily_high_worker is not None and self._daily_high_worker.isRunning()
        missing = tuple(code for code in codes if code not in self._daily_highs)
        if not missing:
            return False
        worker = self._daily_high_worker_factory(missing)
        worker.setParent(self)
        if not isinstance(worker, DailyHighWorker):
            self._on_background_failure("기간 신고가 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요.")
            worker.deleteLater()
            return False
        worker.received.connect(self._on_daily_high_received)
        worker.failed.connect(self._on_background_failure)
        worker.finished.connect(lambda: self._start_fundamentals_phase(codes))
        self._daily_high_worker = worker
        worker.start()
        return True

    def _on_daily_high_received(self, code: str, targets: object) -> None:
        if isinstance(targets, DailyHighTargets):
            self._daily_highs[code] = targets
            if targets.previous_day_trade_value_eok is not None:
                self._previous_day_trade_values[code] = targets.previous_day_trade_value_eok
            if self._defer_table_update_while_modal():
                return
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
            item.setBackground(
                QColor("#F4CCCC") if is_alert and code not in self._near_high_codes
                else self._row_background_color(code, row)
            )
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
                strength = trade_strength_percent(value, fundamentals)
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
        period_labels = {"1m": "1분", "5m": "5분", "60m": "60분", "day": "1일"}
        self.statusBar().showMessage(f"상위 테마 거래대금 기준: {period_labels[next_period]}")

    def _refresh_theme_trade_summary(self) -> None:
        if self._settings.get("theme_trade_summary_enabled") != "1":
            self._theme_trade_summary.setVisible(False)
            self._theme_trade_excluded_summary.setVisible(False)
            return
        self._theme_trade_summary.setVisible(True)
        if not self._ranked_stock_names:
            self._theme_trade_summary.setText("상위 테마 거래대금: 순위 조회 후 표시됩니다.")
            self._theme_trade_excluded_summary.setVisible(False)
            return
        totals: dict[str, tuple[str, float]] = {}
        now = self._ranking_now()
        period = self._settings.get("theme_trade_summary_period")
        period_labels = {"1m": "1분", "5m": "5분", "60m": "60분", "day": "1일"}
        period = period if period in period_labels else "day"
        minutes = {"1m": 1, "5m": 5, "60m": 60}
        for code, name in self._ranked_stock_names.items():
            value = self._minute_aggregator.today_trade_value_eok(code, now) if period == "day" else self._minute_aggregator.bucket_trade_value_eok(code, minutes[period], now)
            for theme in parse_themes(self._themes.get("".join(name.split()), ""), ","):
                key = theme.casefold()
                display, previous = totals.get(key, (theme, 0.0))
                totals[key] = (display, previous + value)
        top = sorted(totals.values(), key=lambda item: item[1], reverse=True)[:3]
        if not top:
            self._theme_trade_summary.setText("상위 테마 거래대금: 테마가 지정된 종목이 없습니다.")
            return
        digits = self._decimal_places("trade_value")
        values = self._theme_trade_summary_values(top, digits)
        self._theme_trade_summary.setText(f"상위 테마 거래대금 (실시간 조회 상위 20종목 · {period_labels[period]}): {values}")
        excluded_names = parse_themes(self._settings.get("theme_trade_summary_excluded_stocks"), ",/|;")
        excluded = {"".join(value.split()).casefold() for value in excluded_names}
        if not excluded or self._settings.get("theme_trade_summary_excluded_enabled") != "1":
            self._theme_trade_excluded_summary.setVisible(False)
            return
        excluded_totals: dict[str, tuple[str, float]] = {}
        for code, name in self._ranked_stock_names.items():
            if code.casefold() in excluded or "".join(name.split()).casefold() in excluded:
                continue
            value = self._minute_aggregator.today_trade_value_eok(code, now) if period == "day" else self._minute_aggregator.bucket_trade_value_eok(code, minutes[period], now)
            for theme in parse_themes(self._themes.get("".join(name.split()), ""), ","):
                key = theme.casefold()
                display, previous = excluded_totals.get(key, (theme, 0.0))
                excluded_totals[key] = (display, previous + value)
        excluded_top = sorted(excluded_totals.values(), key=lambda item: item[1], reverse=True)[:3]
        self._theme_trade_excluded_summary.setVisible(True)
        self._theme_trade_excluded_summary.setText(
            f"제외 종목 반영 상위 테마 거래대금 (실시간 조회 상위 20종목 · {period_labels[period]}): "
            f"{self._theme_trade_summary_values(excluded_top, digits) if excluded_top else '표시할 테마가 없습니다.'}"
        )

    def _theme_trade_summary_values(self, top: list[tuple[str, float]], digits: int) -> str:
        return "&nbsp;&nbsp;".join(
            f"{index}. {escape(theme)} "
            f"<span style='background:{'#FCE4D6' if value >= 10_000 else '#EAF2F8'}; border:1px solid {'#E6A57E' if value >= 10_000 else '#8FB9D9'}; border-radius:4px; padding:2px 6px; color:{self._trade_value_color(value)}; font-weight:700;'>{self._format_trade_value_eok(value, digits)}</span>"
            for index, (theme, value) in enumerate(top, start=1)
        )

    @staticmethod
    def _format_trade_value_eok(value: float, digits: int) -> str:
        if value < 10_000:
            return f"{value:,.{digits}f}억"
        jo = int(value // 10_000)
        remainder = value - jo * 10_000
        if remainder < 0.005:
            return f"{jo:,}조"
        formatted = f"{remainder:,.{digits}f}"
        if digits:
            whole, _, fraction = formatted.partition(".")
            formatted = whole if not fraction.rstrip("0") else f"{whole}.{fraction.rstrip('0')}"
        return f"{jo:,}조{formatted}억"

    @staticmethod
    def _trade_value_color(value: float) -> str:
        return "#C00000" if value >= 10_000 else "#0070C0"
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
                item.setBackground(color)
            widget = self._table.cellWidget(row, column)
            if widget is not None:
                palette = widget.palette()
                palette.setColor(QPalette.ColorRole.Window, color)
                widget.setPalette(palette)
                widget.setAutoFillBackground(True)
                widget.setStyleSheet(f"background-color: {color.name()};")

    def _row_background_color(self, code: str, row: int) -> QColor:
        if code in self._near_high_codes:
            return QColor("#FDE9E7")
        if code in self._rank_changed_codes and self._settings.get("rank_changed_highlight_enabled") == "1":
            return QColor(self._settings.get("rank_changed_row_color"))
        rank_item = self._table.item(row, 0) if hasattr(self, "_table") else None
        try:
            rank = int(rank_item.text()) if rank_item is not None else row + 1
        except ValueError:
            rank = row + 1
        key = "rank_row_odd_color" if rank % 2 else "rank_row_even_color"
        return QColor(self._settings.get(key))

    def _start_rank_changed_highlights(self) -> None:
        self._rank_changed_highlight_timer.stop()
        if not self._rank_changed_codes:
            return
        if self._settings.get("rank_changed_highlight_enabled") != "1":
            self._clear_rank_changed_highlights()
            return
        try:
            duration_ms = round(float(self._settings.get("rank_changed_highlight_seconds")) * 1000)
        except ValueError:
            duration_ms = 2_000
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
        try:
            return max(0, min(8 if column == "strength" else 4, int(self._settings.get(f"decimal_{column}"))))
        except ValueError:
            return 2

    @staticmethod
    def _change_rate_item(value: object) -> QTableWidgetItem:
        item = QTableWidgetItem(str(value))
        try:
            rate = float(str(value).strip().replace("%", "").replace(",", ""))
        except ValueError:
            return item
        if rate > 0:
            item.setForeground(QColor("#C00000"))
        elif rate < 0:
            item.setForeground(QColor("#0070C0"))
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
        if self._closing or self._ranking_priority_preparing or self._fundamentals_worker_factory is None:
            return False
        if self._fundamentals_worker is not None and self._fundamentals_worker.isRunning():
            return True
        missing_codes = codes if force else tuple(code for code in codes if code not in self._fundamentals)
        if not force and self._stock_lookup is not None and hasattr(self._stock_lookup, "fundamentals_to_refresh"):
            missing_codes = tuple(self._stock_lookup.fundamentals_to_refresh(codes, self._ranking_now().strftime("%Y-%m-%d")))
        if not missing_codes:
            return False
        worker = self._fundamentals_worker_factory(missing_codes)
        worker.setParent(self)
        if not isinstance(worker, FundamentalsWorker):
            self._on_background_failure("기본정보 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요.")
            worker.deleteLater()
            return False
        worker.received.connect(self._on_fundamentals_received)
        worker.failed.connect(self._on_background_failure)
        worker.completed.connect(self._on_fundamentals_completed)
        self._fundamentals_worker = worker
        self._pending_secondary_codes = codes
        worker.start()
        return True

    def _on_fundamentals_received(self, code: str, fundamentals: object) -> None:
        if isinstance(fundamentals, StockFundamentals):
            self._fundamentals[code] = fundamentals
            if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_fundamentals"):
                self._stock_lookup.update_fundamentals(code, fundamentals.market_cap_eok, fundamentals.float_ratio_percent, fundamentals.high_250_price)
            if fundamentals.current_price is not None:
                self._current_prices[code] = fundamentals.current_price
                self._pending_price_cache[code] = fundamentals.current_price
                if not self._price_cache_timer.isActive():
                    self._price_cache_timer.start()
            if self._defer_table_update_while_modal():
                return
            if fundamentals.current_price is not None:
                row = self._row_by_code.get(code)
                if row is not None:
                    self._render_current_price(code)
            self._render_market_cap(code)
            self._render_new_high_price(code)
            self._render_high_distance(code)
            self._render_trade_values(code)

    def _render_market_cap(self, code: str) -> None:
        row = self._row_by_code.get(code)
        fundamentals = self._fundamentals.get(code)
        if row is None or fundamentals is None:
            return
        level = self._market_cap_highlight_level(fundamentals.market_cap_eok)
        item = QTableWidgetItem(self._format_market_cap_eok(fundamentals.market_cap_eok))
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setForeground(QColor(self._market_cap_highlight_color(fundamentals.market_cap_eok)))
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
        """시가총액은 조·억 경계를 눈에 띄게 구분해 표시한다."""
        text = self._format_trade_value_eok(value, 0)
        return text.replace("조", "조 · ", 1) if "조" in text and text.endswith("억") else text

    def _on_fundamentals_completed(self) -> None:
        codes = getattr(self, "_pending_secondary_codes", ())
        self._pending_secondary_codes = ()
        if not self._closing and codes:
            if self._is_after_hours_data_pause():
                self._start_nxt_phase(codes)
            else:
                self._start_initial_new_high_refresh(codes)

    def _on_realtime_failure(self, message: str) -> None:
        logger.warning("실시간 체결 연결 실패: %s", message)
        self.statusBar().showMessage("실시간 체결 연결에 실패했습니다. 새로고침으로 다시 시도하세요.")

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closing:
            self._closing = True
            self._window_geometry_save_timer.stop()
            self._save_window_geometry()
            self._save_columns()
            self._ranking_timer.stop()
            self._rank_changed_highlight_timer.stop()
            self._realtime_session_timer.stop()
            self._ranking_preparation_timer.stop()
            self._clock_timer.stop()
            self._theme_trade_summary_timer.stop()
            self._price_cache_timer.stop()
            self._google_drive_debounce.stop()
            self._save_current_price_cache()
            if hasattr(self, "_trade_tick_flush_timer"):
                self._trade_tick_flush_timer.stop()
            for player, _ in self._near_high_sound_players.values():
                player.stop()
            self._refresh_button.setEnabled(False)
            self.statusBar().showMessage("종료 중: 실행 중인 작업을 일시 중지하고 있습니다…")
            if self._google_drive_sync is not None and self._google_drive_sync.connected and self._settings.get("google_drive_auto_upload_on_exit") == "1" and (self._google_drive_dirty or self._google_drive_debounce.isActive()):
                self._start_google_drive_sync("upload", close_after=True)
            self._request_worker_stop()
            QTimer.singleShot(100, self._finish_shutdown)
            event.ignore()
            return
        if self._running_workers():
            event.ignore()
            return
        event.accept()

    def _workers(self) -> tuple[QThread | None, ...]:
        return (
            self._realtime_worker,
            self._minute_history_worker,
            self._fundamentals_worker,
            self._daily_high_worker,
            self._nxt_eligibility_worker,
            self._new_high_worker,
            self._ranking_worker,
            getattr(self, "_image_theme_ocr_worker", None),
            getattr(self, "_krx_stock_catalog_worker", None),
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

    def _apply_table_visuals(self) -> None:
        if not hasattr(self, "_table"):
            return
        palette = self._table.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(self._settings.get("rank_row_odd_color")))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(self._settings.get("rank_row_even_color")))
        self._table.setPalette(palette)
        self._table.setAlternatingRowColors(True)
        font_size = int(self._settings.get("ui_font_size"))
        row_height = int(self._settings.get("ui_row_height"))
        if font_size:
            font = self._table.font(); font.setPointSize(font_size); self._table.setFont(font)
        elif self._settings.get("ui_mode") == "responsive":
            font = self._table.font(); font.setPointSize(max(9, min(13, self.width() // 130))); self._table.setFont(font)
        if row_height:
            self._table.verticalHeader().setDefaultSectionSize(row_height)
        elif self._settings.get("ui_mode") == "responsive":
            # 자동 UI는 가로뿐 아니라 창 높이와 현재 표시 행 수에도 맞춘다.
            # 너무 작아져 읽기 어려운 경우와 지나치게 커지는 경우는 제한한다.
            row_count = max(1, self._table.rowCount())
            available_height = self._table.viewport().height()
            fitted_height = available_height // row_count if available_height > 0 else 0
            fallback_height = self._table.font().pointSize() * 2 + 10
            self._table.verticalHeader().setDefaultSectionSize(max(22, min(64, fitted_height or fallback_height)))
        icon_size = max(14, min(32, self._table.verticalHeader().defaultSectionSize() - 6))
        self._table.setIconSize(QSize(icon_size, icon_size))

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
        if watched is self._table.viewport() and getattr(event, "type", lambda: None)() == QEvent.Type.MouseButtonDblClick:
            position = getattr(event, "position", lambda: None)()
            if position is not None:
                point = position.toPoint()
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
        target_width = self.width() + desired_table_width - self._table.width()
        target_height = self.height() + desired_table_height - self._table.height()
        self.resize(max(self.minimumWidth(), target_width), max(self.minimumHeight(), target_height))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_window_geometry_save_timer"):
            self._window_geometry_save_timer.start()
        # 수동으로 열 폭을 조정한 뒤 30초 동안은 창을 어떻게 조절해도
        # 행 높이·열 너비를 모두 유지한다.
        if time.monotonic() < self._manual_column_resize_until:
            return
        # 유예 시간이 지난 뒤에는 반응형 행 높이를 다시 계산한다.
        self._apply_table_visuals()
        if event.size().width() != event.oldSize().width():
            QTimer.singleShot(0, self._resize_columns_proportionally)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # show 과정에서 예약된 자동 맞춤은 아직 막혀 있다. 모두 끝난 뒤부터
        # 실제 사용자의 창 크기 변경에만 자동 맞춤을 허용한다.
        if not self._column_auto_fit_ready:
            QTimer.singleShot(0, self._enable_initial_column_auto_fit)

    def _save_window_geometry(self) -> None:
        """창 크기는 Drive와 무관하게 현재 컴퓨터에 즉시 보관한다."""
        self._settings.set("window_width", str(self.width()))
        self._settings.set("window_height", str(self.height()))
