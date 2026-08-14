from __future__ import annotations

import logging
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from PySide6.QtGui import QCloseEvent, QResizeEvent, QColor, QDesktopServices, QIcon, QPainter, QPolygon
from PySide6.QtCore import QProcess, QThread, QTimer, QUrl, QSize, QPoint
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
    QMenu,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
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
from kiwoom_monitor.infrastructure.excel.theme_repository import ThemeRepository
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting, ColumnSettingsRepository
from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupError, SettingsBackupService
from kiwoom_monitor.infrastructure.excel.theme_repository import ThemeRepository as ExcelThemeRepository
from kiwoom_monitor.domain.theme_import import validate_theme_header, validate_theme_rows
from kiwoom_monitor.domain.theme_parser import parse_themes
from kiwoom_monitor.presentation.theme_colors import text_color
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import ApiProfiles, LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomApiError, KiwoomRestClient, KiwoomSettings
from kiwoom_monitor.domain.strength_level import strength_badge
from kiwoom_monitor.application.theme_matching import MatchedThemeRow, match_theme_rows
from kiwoom_monitor.application.theme_preview import preview_theme_changes
from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator


class RankingLoader(Protocol):
    def load_top_stocks(self) -> tuple[object, ...]: ...


logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsRepository, api_path: Path | None = None, log_opener: Callable[[], None] | None = None, theme_manager_opener: Callable[[], None] | None = None, parent: QWidget | None = None, column_manager_opener: Callable[[], None] | None = None, backup_exporter: Callable[[], None] | None = None, backup_importer: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._api_path = api_path
        self._log_opener = log_opener
        self._theme_manager_opener = theme_manager_opener
        self._column_manager_opener = column_manager_opener
        self._backup_exporter = backup_exporter
        self._backup_importer = backup_importer
        self.api_changed = False
        self.setWindowTitle("기본 설정")

        self._rank_query_type = QComboBox()
        self._rank_query_type.addItem("30초", "5")
        self._rank_query_type.addItem("1분", "1")
        self._rank_query_type.addItem("10분", "2")
        self._rank_query_type.addItem("1시간", "3")
        self._rank_query_type.addItem("당일 누적", "4")
        saved_rank_query = settings.get("rank_query_type")
        self._rank_query_type.setCurrentIndex(("5", "1", "2", "3", "4").index(saved_rank_query) if saved_rank_query in {"1", "2", "3", "4", "5"} else 0)
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
        self._badge_font_size=QLineEdit(settings.get("theme_badge_font_size")); self._badge_font_size.setPlaceholderText("0: 자동")
        self._badge_padding=QLineEdit(settings.get("theme_badge_padding"))
        self._show_server_clock = QCheckBox("오른쪽 하단 시간 표시")
        self._show_server_clock.setChecked(settings.get("show_server_clock") == "1")
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
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _build_grouped_layout(self) -> None:
        self.resize(520, 500)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        strength_tab = QWidget(); strength_form = QFormLayout(strength_tab)
        strength_form.addRow(QLabel("기간별 거래대금 차이를 반영해 각각 기준을 적용합니다. (%)"))
        strength_form.addRow("거래강도 계산 기준", self._strength_display_mode)
        for period, label in self._strength_period_labels.items():
            interest, caution, fire = self._strength_fields[period]
            strength_form.addRow(f"{label} 관심 / 주의 / 불", self._strength_row(interest, caution, fire))
        strength_form.addRow(self._section_separator())
        strength_form.addRow(QLabel("거래대금 빨간 강조 기준(억원, 0: 끔)"))
        for period, label in self._strength_period_labels.items():
            strength_form.addRow(f"{label}(억)", self._trade_value_alert_fields[period])
        strength_form.addRow(self._strength_icons)
        strength_form.addRow(self._section_separator())
        strength_form.addRow(QLabel("단계 아이콘: 문자를 바꾸거나 이미지 파일을 선택할 수 있습니다. 권장: 투명 PNG, 정사각형 32×32 또는 64×64"))
        for level, label in (("interest", "관심"), ("caution", "주의"), ("fire", "불")):
            strength_form.addRow(f"{label} 아이콘", self._strength_icon_row(level))
        strength_reset = QPushButton("이 탭 초기화")
        strength_reset.clicked.connect(self._reset_strength_settings)
        strength_form.addRow(strength_reset)
        tabs.addTab(strength_tab, "거래강도")

        high_tab = QWidget(); high_form = QFormLayout(high_tab)
        high_form.addRow(QLabel("신고가와 신고가%에 함께 적용됩니다."))
        high_form.addRow("신고가 기준", self._high_distance_period)
        high_form.addRow("신고가 근접 관심 / 주의 / 불(%)", self._strength_row(self._near_high_fields["interest"], self._near_high_fields["caution"], self._near_high_fields["fire"]))
        high_form.addRow("전체 행 빨간 강조 단계", self._near_high_row_alert_level)
        high_form.addRow(self._near_high_enabled)
        high_form.addRow(self._near_high_icons)
        high_form.addRow(QLabel("근접 아이콘: 문자를 바꾸거나 이미지 파일을 선택할 수 있습니다. 권장: 투명 PNG, 정사각형 32×32 또는 64×64"))
        for level, label in (("interest", "관심"), ("caution", "주의"), ("fire", "불")):
            high_form.addRow(f"{label} 아이콘", self._near_high_icon_row(level))
        high_form.addRow(self._near_high_sounds)
        high_form.addRow(QLabel("소리 파일: 해당 단계에 새로 진입할 때 한 번만 재생됩니다. WAV·MP3 권장"))
        for level, label in (("interest", "관심"), ("caution", "주의"), ("fire", "불")):
            high_form.addRow(f"{label} 소리", self._near_high_sound_row(level))
        high_reset = QPushButton("이 탭 초기화")
        high_reset.clicked.connect(self._reset_high_settings)
        high_form.addRow(high_reset)
        tabs.addTab(high_tab, "신고가")

        ui_tab = QWidget(); ui_form = QFormLayout(ui_tab)
        ui_form.addRow("순위 조회 기준", self._rank_query_type)
        ui_form.addRow("화면 모드", self._ui_mode)
        ui_form.addRow(self._section_separator())
        ui_form.addRow("표 글자 크기(0: 자동)", self._font_size)
        ui_form.addRow("행 높이(0: 자동)", self._row_height)
        ui_form.addRow("테마 배지 글자 크기(0: 자동)", self._badge_font_size)
        ui_form.addRow("테마 배지 여백", self._badge_padding)
        ui_form.addRow(self._show_server_clock)
        ui_form.addRow(self._section_separator())
        ui_form.addRow("등락률 소수점", self._decimal_fields["change_rate"])
        ui_form.addRow("거래대금 소수점", self._decimal_fields["trade_value"])
        ui_form.addRow("거래강도 소수점", self._decimal_fields["strength"])
        ui_form.addRow("신고가% 소수점", self._decimal_fields["high_distance"])
        ui_reset = QPushButton("이 탭 초기화")
        ui_reset.clicked.connect(self._reset_ui_settings)
        ui_form.addRow(ui_reset)
        tabs.addTab(ui_tab, "화면 설정")

        columns_tab = QWidget(); columns_form = QFormLayout(columns_tab)
        columns_form.addRow(QLabel("표에 표시할 항목을 한 번에 고르고, 표시 순서를 바꿉니다."))
        if self._column_manager_opener is not None:
            columns_button = QPushButton("표 구성 열기")
            columns_button.clicked.connect(self._column_manager_opener)
            columns_form.addRow(columns_button)
        tabs.addTab(columns_tab, "표 구성")

        manage_tab = QWidget(); manage_form = QFormLayout(manage_tab)
        if self._api_path is not None:
            api_button = QPushButton("API 설정")
            api_button.clicked.connect(self._open_api_settings)
            manage_form.addRow("API", api_button)
        if self._log_opener is not None:
            log_button = QPushButton("로그 폴더 열기")
            log_button.clicked.connect(self._log_opener)
            manage_form.addRow("모니터링 로그", log_button)
        if self._theme_manager_opener is not None:
            theme_button = QPushButton("종목/테마 관리")
            theme_button.clicked.connect(self._theme_manager_opener)
            manage_form.addRow("종목/테마", theme_button)
        if self._backup_exporter is not None:
            backup_button = QPushButton("설정 백업 저장")
            backup_button.clicked.connect(self._backup_exporter)
            manage_form.addRow("설정 백업", backup_button)
        if self._backup_importer is not None:
            restore_button = QPushButton("설정 백업 불러오기")
            restore_button.clicked.connect(self._backup_importer)
            manage_form.addRow("설정 복원", restore_button)
        tabs.addTab(manage_tab, "관리")
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reset_strength_settings(self) -> None:
        for period, fields in self._strength_fields.items():
            for level, field in zip(("interest", "caution", "fire"), fields):
                field.setText(DEFAULT_SETTINGS[f"strength_{period}_{level}"])
            self._trade_value_alert_fields[period].setText(DEFAULT_SETTINGS[f"trade_value_{period}_alert_eok"])
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
            self._near_high_sound_paths[level] = ""
            self._update_near_high_sound_label(level)
        self._near_high_enabled.setChecked(DEFAULT_SETTINGS["near_high_alert_enabled"] == "1")

    def _reset_ui_settings(self) -> None:
        self._rank_query_type.setCurrentIndex(("5", "1", "2", "3", "4").index(DEFAULT_SETTINGS["rank_query_type"]))
        self._ui_mode.setCurrentIndex(0 if DEFAULT_SETTINGS["ui_mode"] == "responsive" else 1)
        self._font_size.setText(DEFAULT_SETTINGS["ui_font_size"])
        self._row_height.setText(DEFAULT_SETTINGS["ui_row_height"])
        self._badge_font_size.setText(DEFAULT_SETTINGS["theme_badge_font_size"])
        self._badge_padding.setText(DEFAULT_SETTINGS["theme_badge_padding"])
        self._show_server_clock.setChecked(DEFAULT_SETTINGS["show_server_clock"] == "1")
        for key, field in self._decimal_fields.items():
            field.setCurrentText(DEFAULT_SETTINGS[f"decimal_{key}"])

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
        suffix = Path(source).suffix.lower() or ".png"
        destination = root / "data" / "strength_icons" / f"{level}{suffix}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        except OSError as error:
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
        suffix = Path(source).suffix.lower() or ".png"
        destination = root / "data" / "near_high_icons" / f"{level}{suffix}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        except OSError as error:
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
        root = self._api_path.parent.parent if self._api_path is not None else Path(__file__).resolve().parents[3]
        suffix = Path(source).suffix.lower() or ".wav"
        destination = root / "data" / "near_high_sounds" / f"{level}{suffix}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
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
    def _section_separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #B8B8B8; border: 0;")
        return line

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
        except ValueError:
            QMessageBox.warning(self, "입력 확인", "화면 크기 설정은 0 이상의 정수로 입력하세요.")
            return
        if not (0 <= font_size <= 30 and 0 <= row_height <= 100 and 0 <= badge_font_size <= 30 and 0 <= badge_padding <= 20):
            QMessageBox.warning(self, "입력 확인", "화면 크기 설정 범위를 확인하세요.")
            return
        self._settings.set("rank_query_type", str(self._rank_query_type.currentData()))
        self._settings.set("ui_mode", str(self._ui_mode.currentData()))
        for period, (interest, caution, fire) in strength_thresholds.items():
            self._settings.set(f"strength_{period}_interest", str(interest))
            self._settings.set(f"strength_{period}_caution", str(caution))
            self._settings.set(f"strength_{period}_fire", str(fire))
        for period, value in trade_value_alerts.items():
            self._settings.set(f"trade_value_{period}_alert_eok", str(value))
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
        self._settings.set("ui_font_size", str(font_size)); self._settings.set("ui_row_height", str(row_height)); self._settings.set("theme_badge_font_size", str(badge_font_size)); self._settings.set("theme_badge_padding", str(badge_padding))
        for key, field in self._decimal_fields.items():
            self._settings.set(f"decimal_{key}", field.currentText())
        self._settings.set("near_high_alert_enabled", "1" if self._near_high_enabled.isChecked() else "0")
        self._settings.set("strength_show_icon", "1" if self._strength_icons.isChecked() else "0")
        for level, field in self._strength_icon_fields.items():
            self._settings.set(f"strength_icon_{level}", field.text().strip())
            self._settings.set(f"strength_icon_{level}_image", self._strength_icon_images[level])
        self._settings.set("strength_display_mode", str(self._strength_display_mode.currentData()))
        self._settings.set("show_server_clock", "1" if self._show_server_clock.isChecked() else "0")
        self._settings.set("high_distance_period", str(self._high_distance_period.currentData()))
        self.accept()


class ColumnManagerDialog(QDialog):
    """기본 설정에서 여러 열의 표시 여부와 순서를 한 번에 편집한다."""

    def __init__(self, repository: ColumnSettingsRepository, columns: tuple[tuple[str, str], ...], table: QTableWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repository = repository
        self._columns = columns
        self._table = table
        self.setWindowTitle("표 구성")
        self.resize(360, 440)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("체크하면 표시합니다. 항목을 위아래로 드래그해 순서를 바꿀 수 있습니다."))
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

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self._list.count()):
            self._list.item(row).setCheckState(state)

    def _move_current(self, offset: int) -> None:
        row = self._list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self._list.count():
            return
        item = self._list.takeItem(row)
        self._list.insertItem(target, item)
        self._list.setCurrentRow(target)

    def _save(self) -> None:
        settings: list[ColumnSetting] = []
        for position in range(self._list.count()):
            item = self._list.item(position)
            logical = int(item.data(Qt.ItemDataRole.UserRole))
            name, _ = self._columns[logical]
            settings.append(ColumnSetting(name, item.checkState() == Qt.CheckState.Checked, position, self._table.columnWidth(logical)))
        if not any(setting.visible for setting in settings):
            QMessageBox.warning(self, "표 구성", "최소 한 개의 열은 표시해야 합니다.")
            return
        self._repository.save(tuple(settings))
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
    def __init__(self, changes: tuple[object, ...], skipped: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Excel 테마 변경 미리보기")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        unchanged=sum(getattr(change, "status", "") == "변경 없음" for change in changes); new=sum(getattr(change, "status", "") == "신규" for change in changes); changed=sum(getattr(change, "status", "") == "테마 변경" for change in changes)
        layout.addWidget(QLabel(f"전체 {len(changes) + skipped} · 변경 없음 {unchanged} · 신규 {new} · 테마 변경 {changed} · 오류/제외 {skipped}"))
        table = QTableWidget(len(changes), 4)
        table.setHorizontalHeaderLabels(("종목", "기존 테마", "변경 테마", "상태"))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for index, change in enumerate(changes):
            table.setItem(index, 0, QTableWidgetItem(str(getattr(change, "name", ""))))
            table.setItem(index, 1, QTableWidgetItem(", ".join(getattr(change, "before", ()))))
            table.setItem(index, 2, QTableWidgetItem(", ".join(getattr(change, "after", ()))))
            table.setItem(index, 3, QTableWidgetItem(str(getattr(change, "status", ""))))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


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

class ThemeManagerDialog(QDialog):
    def __init__(self, repository: object, settings: SettingsRepository, on_excel_update: Callable[[], None] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent); self._repository=repository; self._settings=settings; self._separators=",/|;" + settings.get("theme_custom_separators"); self._on_excel_update=on_excel_update; self.setWindowTitle("종목/테마 관리"); self.resize(560,420)
        self._search=QLineEdit(); self._search.setPlaceholderText("종목명 검색"); self._table=QTableWidget(0,2); self._table.setHorizontalHeaderLabels(("종목명","테마"))
        self._custom_separators = QLineEdit(settings.get("theme_custom_separators")); self._custom_separators.setPlaceholderText("기본 , / | ; 외에 사용할 구분 문자")
        self._add=QPushButton("신규 종목 테마 추가"); layout=QVBoxLayout(self); layout.addWidget(self._search); layout.addWidget(self._table); layout.addWidget(QLabel("추가 테마 구분자")); layout.addWidget(self._custom_separators); layout.addWidget(self._add)
        self._add.clicked.connect(self._add_new)
        if self._on_excel_update is not None:
            excel = QPushButton("Excel 테마 업데이트")
            excel.clicked.connect(self._on_excel_update)
            layout.addWidget(excel)
        self._color = QPushButton("테마 색상 변경")
        layout.addWidget(self._color)
        self._color.clicked.connect(self._edit_theme_color)
        self._custom_separators.editingFinished.connect(self._save_custom_separators)
        self._search.textChanged.connect(self._reload); self._table.cellDoubleClicked.connect(self._edit); self._rows=(); self._reload()
    def _save_custom_separators(self) -> None:
        value = self._custom_separators.text().strip()
        self._settings.set("theme_custom_separators", value)
        self._separators = ",/|;" + value
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
    def _add_new(self) -> None:
        name,ok=QInputDialog.getText(self,"신규 종목", "종목명")
        if not ok or not name.strip(): return
        code=self._repository.find_code_by_name(name.strip())
        if not code: QMessageBox.warning(self,"종목 매칭 실패","키움 종목 DB에서 찾을 수 없습니다."); return
        themes,ok=QInputDialog.getText(self,"신규 종목", "테마 (, / | ; 구분)")
        if ok and QMessageBox.question(self,"신규 종목 추가 확인",f"{name}\n{themes}\n\n추가할까요?") == QMessageBox.StandardButton.Yes:
            self._repository.replace_for_stock(code, parse_themes(themes, self._separators)); self._reload()
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


class NxtMarkerDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: object, index: object) -> None:
        super().paint(painter, option, index)
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
    COLUMNS = (("rank","순위"),("stock","종목"),("themes","테마"),("change_rate","등락률"),("strength_1m","1분강도"),("current_price","현재가"),("trade_value_1m","1분(억)"),("trade_value_5m","5분(억)"),("trade_value_60m","60분(억)"),("trade_value_day","1일(억)"),("strength_5m","5분강도"),("strength_60m","60분강도"),("strength_day","1일강도"),("new_high_price","신고가"),("high_distance","신고가%"))
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
        self._themes = themes or {}
        self._columns = columns
        self._stock_lookup = stock_lookup
        self._theme_store = theme_store
        self._realtime_worker: RealtimeTradeWorker | None = None
        self._realtime_codes: tuple[str, ...] = ()
        self._minute_history_worker: MinuteHistoryWorker | None = None
        self._fundamentals_worker: FundamentalsWorker | None = None
        self._daily_high_worker: DailyHighWorker | None = None
        self._nxt_eligibility_worker: NxtEligibilityWorker | None = None
        self._new_high_worker: NewHighWorker | None = None
        self._ranking_worker: RankingWorker | None = None
        self._partial_ranking_retry_count = 0
        self._resizing_columns = False
        self._restoring_columns = False
        self._manual_column_resize_until = 0.0
        self._initial_new_high_refresh_started = False
        self._closing = False
        self._row_by_code: dict[str, int] = {}
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
        self._clock_label = QLabel()
        self.statusBar().addPermanentWidget(self._clock_label)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock_label)
        self._clock_timer.start(1000)
        self._update_clock_label()
        self.setWindowTitle("키움 실시간 종목 모니터")
        self.resize(int(self._settings.get("window_width")), int(self._settings.get("window_height")))

        toolbar = QToolBar("도구")
        toolbar.setMovable(False)
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
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
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
        self._table.cellDoubleClicked.connect(self._edit_theme_from_main_table)
        self._table.setItemDelegateForColumn(1, NxtMarkerDelegate(self._table))
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionsMovable(True)
        self._table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.horizontalHeader().customContextMenuRequested.connect(self._show_column_menu)
        self._table.horizontalHeader().sectionMoved.connect(lambda *_: self._save_columns())
        self._table.horizontalHeader().sectionResized.connect(self._on_column_resized)
        self._restore_columns()
        self._table.setItem(0, 1, QTableWidgetItem("새로고침을 눌러 순위를 조회하세요."))

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(self._table)
        self.setCentralWidget(content)
        self._apply_table_visuals()
        message = "새로고침으로 키움 REST 조회를 시작합니다." if ranking_loader else "상단 API 설정에서 키를 입력해 연결할 수 있습니다."
        self.statusBar().showMessage(message)
        if ranking_loader is not None:
            QTimer.singleShot(0, self._refresh_rankings)
            self._schedule_next_ranking_refresh()
        else:
            QTimer.singleShot(0, self._open_api_settings)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self._settings,
            self._api_config_path(),
            self._open_log_file,
            self._open_theme_manager,
            self,
            self._open_column_manager,
            self._export_settings_backup,
            self._import_settings_backup,
        )
        if dialog.exec():
            if self._ranking_loader is not None and hasattr(self._ranking_loader, "set_query_type"):
                self._ranking_loader.set_query_type(self._settings.get("rank_query_type"))
            self._schedule_next_ranking_refresh()
            self._apply_table_visuals()
            self._update_clock_label()
            for code in self._row_by_code:
                current_price = self._current_prices.get(code)
                if current_price is not None:
                    self._set_near_high_level(code, current_price, play_sound=False)
                    self._apply_near_high_background(code)
                self._render_new_high_price(code)
                self._render_high_distance(code)
            self.statusBar().showMessage("기본 설정 저장 완료")
            if dialog.api_changed:
                self._restart_for_api_settings()

    def _open_column_manager(self) -> None:
        if self._columns is None:
            return
        dialog = ColumnManagerDialog(self._columns, self.COLUMNS, self._table, self)
        if dialog.exec():
            self._restore_columns()
            self._table.updateGeometry()
            QTimer.singleShot(0, self._resize_columns_proportionally)
            self.statusBar().showMessage("표 구성을 적용했습니다.")

    def _open_alert_settings(self) -> None:
        if AlertSettingsDialog(self._settings, self).exec():
            enabled = self._settings.get("near_high_alert_enabled") == "1"
            self.statusBar().showMessage("신고가 근접 알림을 " + ("사용합니다." if enabled else "사용하지 않습니다."))

    def _open_theme_manager(self) -> None:
        if self._theme_store is not None:
            ThemeManagerDialog(self._theme_store, self._settings, self._select_excel, self).exec()

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
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
        return root / "data" / "api.env"

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
        if QMessageBox.question(
            self,
            "API 설정 저장 완료",
            "설정을 적용하려면 앱을 다시 열어야 합니다. 지금 다시 열까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            arguments = [] if getattr(sys, "frozen", False) else ["-m", "kiwoom_monitor"]
            QProcess.startDetached(sys.executable, arguments)
            self.close()
        else:
            self.statusBar().showMessage("API 설정이 저장되었습니다. 앱을 다시 열면 적용됩니다.")

    def _select_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "테마 Excel 선택", "", "Excel 파일 (*.xlsx)")
        if path:
            try:
                source = ExcelThemeRepository(Path(path)); header, raw_rows = source.load_header_and_rows()
                separators = ",/|;" + self._settings.get("theme_custom_separators")
                rows, errors = validate_theme_rows(raw_rows, separators)
                errors = validate_theme_header(header) + errors
            except Exception as error:
                self.statusBar().showMessage(f"Excel 읽기 실패: {error}")
                return
            self.statusBar().showMessage(f"Excel 검증 완료 · 유효 {len(rows)}건 · 오류 {len(errors)}건")
            if errors:
                QMessageBox.warning(self, "Excel 검증 오류", "\n".join(errors))
            else:
                matched, unmatched = match_theme_rows(rows, self._stock_lookup) if self._stock_lookup else ((), rows)
                resolved, cancelled = self._resolve_unmatched_theme_rows(unmatched)
                if cancelled:
                    return
                matched = matched + resolved
                changes = preview_theme_changes(matched, self._theme_store) if self._theme_store else ()
                changed = sum(change.status == "테마 변경" for change in changes); new = sum(change.status == "신규" for change in changes)
                preview = ThemePreviewDialog(changes, len(unmatched), self)
                if preview.exec() and self._theme_store:
                    for change in changes:
                        if change.status != "변경 없음": self._theme_store.replace_for_stock(change.code, change.after)
                    self._themes = self._theme_store.all_by_name()
                    self._refresh_rankings()
                unchanged = sum(change.status == "변경 없음" for change in changes)
                self.statusBar().showMessage(f"Excel 결과 · 전체 {len(raw_rows)} · 변경 없음 {unchanged} · 신규 {new} · 테마 변경 {changed} · 오류/제외 {len(unmatched) - len(resolved)}")

    def _resolve_unmatched_theme_rows(self, rows: tuple[object, ...]) -> tuple[tuple[MatchedThemeRow, ...], bool]:
        if not rows or self._stock_lookup is None:
            return (), False
        resolved: list[MatchedThemeRow] = []
        for row in rows:
            original_name = str(getattr(row, "name", ""))
            while True:
                name, ok = QInputDialog.getText(
                    self,
                    "키움 종목명 확인",
                    f"Excel의 '{original_name}' 종목명을 찾지 못했습니다.\n키움에 표시되는 종목명을 입력하세요.\n비워 두면 이번 업데이트에서 제외합니다.",
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
                QMessageBox.warning(self, "종목명 확인", f"'{name}'도 키움에서 조회한 종목 목록에 없습니다. 다시 입력하세요.")
        return tuple(resolved), False

    def _restore_columns(self) -> None:
        if self._columns is None: return
        saved = {s.name: s for s in self._columns.list()}
        header = self._table.horizontalHeader()
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
        if self._settings.get("ui_mode") != "responsive" or time.monotonic() < self._manual_column_resize_until or not hasattr(self, "_table"):
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
        QTimer.singleShot(0, self._resize_columns_proportionally)
        self.statusBar().showMessage("컬럼 표시, 순서, 폭을 기본값으로 초기화했습니다.")

    def _on_ranking_timer(self) -> None:
        self._refresh_rankings()
        self._schedule_next_ranking_refresh()

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
        self._ranking_timer.start(max(100, round((next_time - now).total_seconds() * 1000)))

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
        worker.finished.connect(lambda: self._refresh_button.setEnabled(True))
        self._ranking_worker = worker
        worker.start()

    def _on_ranking_failed(self, message: str) -> None:
        logger.warning("순위 조회에 실패했습니다: %s", message)
        self._set_api_status("API: 오류", "#C00000")
        self.statusBar().showMessage("조회에 실패했습니다. 네트워크와 API 설정을 확인하세요.")

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
            return
        self._partial_ranking_retry_count = 0

        self._table.setRowCount(len(stocks))
        self._set_api_status("API: 연결됨", "#008000")
        self._row_by_code.clear()
        codes = tuple(stock.code for stock in stocks)
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_fundamentals"):
            self._fundamentals.update(self._stock_lookup.load_fundamentals(codes))
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "load_nxt_enabled"):
            saved_nxt = self._stock_lookup.load_nxt_enabled(codes, self._ranking_now().strftime("%Y-%m-%d"))
            self._nxt_checked_codes.update(saved_nxt)
            self._nxt_enabled_codes.update(code for code, enabled in saved_nxt.items() if enabled)
            self._nxt_enabled_codes.difference_update(code for code, enabled in saved_nxt.items() if not enabled)
        for row, stock in enumerate(stocks):
            self._row_by_code[stock.code] = row
            self._new_high_periods[stock.code] = frozenset(getattr(stock, "new_high_periods", ()))
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
            )
            for column, value in enumerate(values):
                item = self._change_rate_item(value) if column == 3 else QTableWidgetItem(value)
                self._table.setItem(row, column, item)
            self._table.item(row, 1).setData(Qt.ItemDataRole.UserRole, stock.code)
            self._table.item(row, 1).setData(Qt.ItemDataRole.UserRole + 2, stock.code in self._nxt_enabled_codes)
            if stock.code in self._nxt_enabled_codes:
                self._table.item(row, 1).setToolTip("NXT 거래 가능")
            self._table.setCellWidget(row, 2, self._theme_badges(stock.code, stock.name))
        for stock in stocks:
            current_price = self._current_prices.get(stock.code)
            row = self._row_by_code[stock.code]
            if current_price is not None:
                self._table.setItem(row, 5, QTableWidgetItem(f"{current_price:,}"))
            self._render_trade_values(stock.code)
            self._render_new_high_price(stock.code)
            self._render_high_distance(stock.code)
        self.statusBar().showMessage(f"조회 완료 · {len(stocks)}개 종목 · 실시간 체결 데이터 연결 중")
        self._start_realtime_subscription(codes)
        QTimer.singleShot(5_000, lambda: self._start_realtime_followups(codes))
        self._schedule_next_ranking_refresh()
        # 분봉·기본정보 40건 동시 보완은 모의 API 제한을 쉽게 초과하므로,
        # 안정적인 순위 조회가 확인된 뒤 사용자가 따로 실행하는 방식으로 제공한다.

    def _theme_badges(self, code: str, name: str) -> QWidget:
        widget = QWidget(); layout = QHBoxLayout(widget); layout.setContentsMargins(2, 2, 2, 2); layout.setSpacing(3)
        for theme in self._themes.get("".join(name.split()), "").split(","):
            if theme.strip():
                color = self._theme_store.color_for_stock_theme(code, theme.strip()) if self._theme_store else "#DCE6F1"
                badge_size = int(self._settings.get("theme_badge_font_size")); padding = int(self._settings.get("theme_badge_padding"))
                font_style = f"font-size:{badge_size}px;" if badge_size else ""
                badge = QPushButton(theme.strip()); badge.setStyleSheet(f"background:{color}; color:{text_color(color)}; border-radius:5px; padding:{padding}px {padding + 3}px; {font_style}")
                badge.clicked.connect(lambda _, value=theme.strip(), stock_code=code: self._edit_badge_color(stock_code, value))
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
        self._table.setItem(row, 13, QTableWidgetItem(label))

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
        self._refresh_rankings()

    def _on_new_high_refresh_failed(self, message: str) -> None:
        logger.warning("신고가 갱신에 실패했습니다: %s", message)
        self.statusBar().showMessage("신고가 갱신에 실패했습니다. 잠시 후 다시 시도하세요.")

    def _start_realtime_subscription(self, codes: tuple[str, ...]) -> None:
        if self._closing or self._realtime_worker_factory is None:
            return
        if self._realtime_worker is not None and self._realtime_worker.isRunning() and codes == self._realtime_codes:
            return
        if self._realtime_worker is not None:
            if not self._realtime_worker.stop():
                self._on_background_failure("이전 실시간 연결을 아직 종료하는 중입니다. 잠시 후 다시 시도합니다.")
                return
        worker = self._realtime_worker_factory(codes)
        worker.setParent(self)
        worker.trade_received.connect(self._on_trade_tick)
        worker.status_changed.connect(self.statusBar().showMessage)
        worker.connection_failed.connect(self._on_realtime_failure)
        worker.subscription_ready.connect(lambda: self._start_realtime_followups(codes))
        self._realtime_worker = worker
        self._realtime_codes = codes
        worker.start()

    def _start_realtime_followups(self, codes: tuple[str, ...]) -> None:
        """Begin the visible NXT marker lookup before long background backfills."""
        if self._closing:
            return
        self._start_nxt_eligibility_loading(codes)
        self._start_secondary_loading(codes)

    def _on_trade_tick(self, tick: TradeTick) -> None:
        row = self._row_by_code.get(tick.code)
        if row is None or tick.current_price is None:
            return
        self._table.setItem(row, 5, QTableWidgetItem(f"{tick.current_price:,}"))
        if tick.change_rate is not None:
            self._table.setItem(row, 3, self._change_rate_item(f"{tick.change_rate:+.{self._decimal_places('change_rate')}f}%"))
        self._current_prices[tick.code] = tick.current_price
        self._render_high_distance(tick.code)
        if tick.high_price and tick.high_price > 0:
            self._today_high_prices[tick.code] = tick.high_price
            self._render_new_high_price(tick.code)
            self._render_high_distance(tick.code)
            if tick.current_price >= tick.high_price:
                self._today_high_codes.add(tick.code)
        self._set_near_high_level(tick.code, tick.current_price)
        self._apply_near_high_background(tick.code)
        self._render_high_distance(tick.code)
        observed_at = self._ranking_now()
        bar = self._minute_aggregator.ingest(tick, observed_at)
        if bar is not None or tick.code in self._row_by_code:
            self._render_trade_values(tick.code, live_only=tick.code not in self._minute_history_codes)

    def _start_secondary_loading(self, codes: tuple[str, ...]) -> None:
        """Start non-realtime API work in the defined priority order."""
        if not self._start_minute_history_loading(codes):
            self._start_daily_high_phase(codes)

    def _start_daily_high_phase(self, codes: tuple[str, ...]) -> None:
        if not self._start_daily_high_loading(codes):
            self._start_fundamentals_phase(codes)

    def _start_fundamentals_phase(self, codes: tuple[str, ...]) -> None:
        if not self._start_fundamentals_loading(codes):
            self._start_nxt_phase(codes)

    def _start_nxt_phase(self, codes: tuple[str, ...]) -> None:
        if not self._start_nxt_eligibility_loading(codes):
            self._start_initial_new_high_refresh()

    def _start_initial_new_high_refresh(self) -> None:
        if not self._initial_new_high_refresh_started:
            self._initial_new_high_refresh_started = True
            self._start_new_high_refresh()

    def _start_minute_history_loading(self, codes: tuple[str, ...]) -> bool:
        if self._closing or self._minute_history_worker_factory is None:
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
        self._apply_near_high_background(code)
        self._render_trade_values(code)

    def _start_nxt_eligibility_loading(self, codes: tuple[str, ...]) -> bool:
        if self._closing or self._nxt_eligibility_worker_factory is None:
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
        worker.finished.connect(self._start_initial_new_high_refresh)
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
        distance = max(0.0, (target - current_price) / target * 100)
        level = self._near_high_levels.get(code, "")
        text = f"{distance:.{self._decimal_places('high_distance')}f}%"
        image = self._near_high_icon_image_path(level) if level and self._settings.get("near_high_show_icon") == "1" else None
        if level and image is None and self._settings.get("near_high_show_icon") == "1":
            text += f" {self._settings.get(f'near_high_icon_{level}')}"
        item = QTableWidgetItem(text)
        if image is not None:
            item.setIcon(QIcon(str(image)))
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
        if level and level == self._settings.get("near_high_row_alert_level"):
            self._near_high_codes.add(code)
        else:
            self._near_high_codes.discard(code)
        if level:
            self._near_high_levels[code] = level
        else:
            self._near_high_levels.pop(code, None)
        if play_sound and level and level != previous:
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
        if self._closing or self._daily_high_worker_factory is None or (self._daily_high_worker is not None and self._daily_high_worker.isRunning()):
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
            self._render_new_high_price(code)
            self._render_high_distance(code)

    def _render_trade_values(self, code: str, *, live_only: bool = False) -> None:
        row = self._row_by_code.get(code)
        if row is None:
            return
        now = self._ranking_now()
        values = (self._minute_aggregator.trade_value_eok(code, 1), self._minute_aggregator.trade_value_eok(code, 5), self._minute_aggregator.trade_value_eok(code, 60), self._minute_aggregator.today_trade_value_eok(code, now))
        visible_values = zip(range(6, 7), ("1m",), values[:1]) if live_only else zip(range(6, 10), ("1m", "5m", "60m", "day"), values)
        for column, period, value in visible_values:
            item = QTableWidgetItem(f"{value:.{self._decimal_places('trade_value')}f}")
            threshold = float(self._settings.get(f"trade_value_{period}_alert_eok"))
            is_alert = threshold > 0 and value >= threshold
            item.setBackground(QColor("#FDE9E7") if code in self._near_high_codes else QColor("#F4CCCC") if is_alert else QColor("white"))
            item.setForeground(QColor("#C00000") if is_alert else QColor("black"))
            font = item.font()
            font.setBold(is_alert)
            item.setFont(font)
            self._table.setItem(row, column, item)
        fundamentals = self._fundamentals.get(code)
        if fundamentals:
            strength_source = values
            if self._settings.get("strength_display_mode") == "completed":
                strength_source = (
                    self._minute_aggregator.completed_trade_value_eok(code, 1, now),
                    self._minute_aggregator.completed_trade_value_eok(code, 5, now),
                    self._minute_aggregator.completed_trade_value_eok(code, 60, now),
                    self._minute_aggregator.completed_today_trade_value_eok(code, now),
                )
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
                if code in self._near_high_codes:
                    item.setBackground(QColor("#FDE9E7"))
                is_fire = strength is not None and strength >= fire
                item.setForeground(QColor("#C00000") if is_fire else QColor("#C65911") if strength is not None and strength >= caution else QColor("#806000") if strength is not None and strength >= interest else QColor("black"))
                font = item.font()
                font.setBold(is_fire)
                item.setFont(font)
                self._table.setItem(row, column, item)
    def _apply_near_high_background(self, code: str) -> None:
        row = self._row_by_code.get(code)
        if row is None:
            return
        color = QColor("#FDE9E7") if code in self._near_high_codes else QColor("white")
        for column in range(self._table.columnCount()):
            item = self._table.item(row, column)
            if item is not None:
                item.setBackground(color)

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

    def _start_fundamentals_loading(self, codes: tuple[str, ...]) -> bool:
        if self._closing or self._fundamentals_worker_factory is None:
            return False
        if self._fundamentals_worker is not None and self._fundamentals_worker.isRunning():
            return True
        missing_codes = tuple(code for code in codes if code not in self._fundamentals)
        if self._stock_lookup is not None and hasattr(self._stock_lookup, "fundamentals_to_refresh"):
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
            self._render_new_high_price(code)
            self._render_high_distance(code)
            if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_fundamentals"):
                self._stock_lookup.update_fundamentals(code, fundamentals.market_cap_eok, fundamentals.float_ratio_percent, fundamentals.high_250_price)
            self._render_trade_values(code)

    def _on_fundamentals_completed(self) -> None:
        codes = getattr(self, "_pending_secondary_codes", ())
        self._pending_secondary_codes = ()
        if not self._closing and codes:
            self._start_nxt_phase(codes)

    def _on_realtime_failure(self, message: str) -> None:
        logger.warning("실시간 체결 연결 실패: %s", message)
        self.statusBar().showMessage("실시간 체결 연결에 실패했습니다. 새로고침으로 다시 시도하세요.")

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closing:
            self._closing = True
            self._settings.set("window_width", str(self.width()))
            self._settings.set("window_height", str(self.height()))
            self._ranking_timer.stop()
            self._refresh_button.setEnabled(False)
            self.statusBar().showMessage("종료 중: 실행 중인 작업을 일시 중지하고 있습니다…")
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
            self.statusBar().showMessage(f"종료 중: 실행 중인 작업 {len(running)}개를 중단하는 중입니다…")
            QTimer.singleShot(250, self._finish_shutdown)
            return
        self.close()

    def _apply_table_visuals(self) -> None:
        if not hasattr(self, "_table"):
            return
        font_size = int(self._settings.get("ui_font_size"))
        row_height = int(self._settings.get("ui_row_height"))
        if font_size:
            font = self._table.font(); font.setPointSize(font_size); self._table.setFont(font)
        elif self._settings.get("ui_mode") == "responsive":
            font = self._table.font(); font.setPointSize(max(9, min(13, self.width() // 130))); self._table.setFont(font)
        if row_height:
            self._table.verticalHeader().setDefaultSectionSize(row_height)
        elif self._settings.get("ui_mode") == "responsive":
            self._table.verticalHeader().setDefaultSectionSize(self._table.font().pointSize() * 2 + 10)
        icon_size = max(14, min(32, self._table.verticalHeader().defaultSectionSize() - 6))
        self._table.setIconSize(QSize(icon_size, icon_size))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_table_visuals()
        QTimer.singleShot(0, self._resize_columns_proportionally)
