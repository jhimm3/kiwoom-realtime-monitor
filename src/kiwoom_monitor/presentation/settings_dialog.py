"""메인 화면의 기본 설정 대화상자."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import QEventLoop, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QColor, QResizeEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from kiwoom_monitor.domain.theme_parser import parse_themes
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.persistence.database import DEFAULT_SETTINGS
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
from kiwoom_monitor.presentation.app_metadata import APP_COPYRIGHT, APP_DISPLAY_NAME, APP_VERSION
from kiwoom_monitor.presentation.similar_stock_dialog import SimilarStockDialog
from kiwoom_monitor.presentation.theme_colors import text_color


HIGH_PERIODS = ("5", "20", "250", "historical")


def selected_high_cycle_periods(value: str) -> tuple[str, ...]:
    """저장된 선택값을 고정된 신고가 순서로 정리한다."""
    selected = {item.strip() for item in value.split(",")}
    periods = tuple(period for period in HIGH_PERIODS if period in selected)
    return periods or HIGH_PERIODS


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsRepository, api_path: Path | None = None, log_opener: Callable[[], None] | None = None, theme_manager_opener: Callable[[], None] | None = None, parent: QWidget | None = None, column_manager_opener: Callable[[], None] | None = None, backup_exporter: Callable[[], None] | None = None, backup_importer: Callable[[], None] | None = None, theme_manager_panel_factory: Callable[[QWidget], QWidget] | None = None, column_manager_panel_factory: Callable[[QWidget], QWidget] | None = None, stock_lookup: object | None = None, drive_connector: Callable[[], None] | None = None, drive_downloader: Callable[[], None] | None = None, drive_uploader: Callable[[], None] | None = None, drive_disconnector: Callable[[], None] | None = None, drive_status: Callable[[], str] | None = None, theme_backup_exporter: Callable[[], None] | None = None, theme_backup_importer: Callable[[], None] | None = None, drive_client_importer: Callable[[], None] | None = None, update_checker: Callable[[], None] | None = None, journal_backup_exporter: Callable[[], None] | None = None, journal_backup_importer: Callable[[], None] | None = None, news_api_settings_opener: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._api_path = api_path
        self._data_source_path = api_path.with_name("data_source.json") if api_path is not None else None
        try:
            self._data_source = DataSourceConfig(self._data_source_path).load() if self._data_source_path else DataSourceSettings()
        except (ValueError, OSError, json.JSONDecodeError):
            self._data_source = DataSourceSettings()
        self._data_source_mode = QComboBox()
        self._data_source_mode.addItem("이 PC에서 키움 API 직접 연결", "local")
        self._data_source_mode.addItem("NAS로 연결", "personal_server")
        visible_mode = self._data_source.mode if self._data_source.mode != "local_server" else "local"
        self._data_source_mode.setCurrentIndex(max(0, self._data_source_mode.findData(visible_mode)))
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
        self._journal_backup_exporter = journal_backup_exporter
        self._journal_backup_importer = journal_backup_importer
        self._news_api_settings_opener = news_api_settings_opener
        self._drive_status_label: QLabel | None = None
        self._google_drive_auto_download = QCheckBox("앱 시작 시 자동 다운로드")
        self._google_drive_auto_download.setChecked(settings.get("google_drive_auto_download") == "1")
        self._google_drive_auto_upload = QCheckBox("변경 후 자동 업로드")
        self._google_drive_auto_upload.setChecked(settings.get("google_drive_auto_upload") == "1")
        self._google_drive_auto_upload_on_exit = QCheckBox("종료 시 자동 업로드 시도 (종료가 늦어질 수 있음)")
        self._google_drive_auto_upload_on_exit.setChecked(settings.get("google_drive_auto_upload_on_exit") == "1")
        self._auto_update_check = QCheckBox("앱 시작 시 업데이트 자동 확인")
        self._auto_update_check.setChecked(settings.get("auto_update_check") == "1")
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
            self._rank_changed_highlight_seconds.setValue(0.0)
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
        self._near_high_sound_cooldown = QSpinBox()
        self._near_high_sound_cooldown.setRange(0, 600)
        self._near_high_sound_cooldown.setSuffix("초")
        self._near_high_sound_cooldown.setToolTip("0초로 설정하면 같은 단계에 다시 진입할 때마다 안내합니다.")
        try:
            self._near_high_sound_cooldown.setValue(max(0, int(float(settings.get("near_high_sound_cooldown_seconds")))))
        except (TypeError, ValueError):
            self._near_high_sound_cooldown.setValue(int(DEFAULT_SETTINGS["near_high_sound_cooldown_seconds"]))
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
        self._theme_group_sort_basis = QComboBox()
        self._theme_group_sort_basis.addItem("상위 테마", "all")
        self._theme_group_sort_basis.addItem("제외 종목 반영 상위 테마", "excluded")
        self._theme_group_sort_basis.setCurrentIndex(1 if settings.get("theme_group_sort_basis") == "excluded" else 0)
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
        self._high_distance_period=QComboBox(); self._high_distance_period.addItem("5일 신고가", "5"); self._high_distance_period.addItem("20일 신고가", "20"); self._high_distance_period.addItem("250일 신고가(52주 근사)", "250"); self._high_distance_period.addItem("역사적 신고가(85년 이후)", "historical")
        saved_period=settings.get("high_distance_period"); self._high_distance_period.setCurrentIndex(HIGH_PERIODS.index(saved_period) if saved_period in HIGH_PERIODS else 2)
        cycle_periods = selected_high_cycle_periods(settings.get("high_header_cycle_periods"))
        self._high_header_cycle_checks = {}
        for period, label in zip(HIGH_PERIODS, ("5일", "20일", "250일", "역사적")):
            checkbox = QCheckBox(label)
            checkbox.setChecked(period in cycle_periods)
            self._high_header_cycle_checks[period] = checkbox

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
        cycle_row = QHBoxLayout()
        for checkbox in self._high_header_cycle_checks.values():
            cycle_row.addWidget(checkbox)
        cycle_row.addStretch()
        high_form.addRow("헤더 클릭 순환", cycle_row)
        high_form.addRow(QLabel("선택한 신고가만 5일 → 20일 → 250일 → 역사적 순서로 전환됩니다."))
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
        high_form.addRow("같은 단계 재알림 대기 시간", self._near_high_sound_cooldown)
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
        display_form.addRow("테마 동률 정렬 기준", self._theme_group_sort_basis)
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

        external_tab = QWidget(); external_form = QFormLayout(external_tab)
        external_form.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        external_form.addRow(self._section_title("API 연결"))
        external_form.addRow("데이터 연결 방식", self._data_source_mode)
        if self._api_path is not None:
            api_button = QPushButton("키움 API 설정")
            api_button.clicked.connect(lambda: self._open_api_settings("kiwoom"))
            external_form.addRow("키움", api_button)
            nas_button = QPushButton("NAS 연결 설정")
            nas_button.clicked.connect(lambda: self._open_api_settings("nas"))
            external_form.addRow("NAS", nas_button)
        if self._news_api_settings_opener is not None:
            news_api_button = QPushButton("네이버·DART·AI API 연결")
            news_api_button.setObjectName("news_api_settings_button")
            news_api_button.clicked.connect(self._news_api_settings_opener)
            external_form.addRow("뉴스·AI", news_api_button)
        if self._drive_connector is not None:
            external_form.addRow(self._section_separator())
            external_form.addRow(self._section_title("Google 계정 연결"))
            status = QLabel(); status.setWordWrap(False)
            self._drive_status_label = status
            self.refresh_drive_status()
            if self._drive_client_importer is not None:
                client_file = QPushButton("내 OAuth JSON 연결")
                client_file.clicked.connect(self._drive_client_importer)
                external_form.addRow("OAuth 구성", client_file)
            connect = QPushButton("Google Drive 연결")
            connect.clicked.connect(self._drive_connector)
            external_form.addRow("연결", connect)
            external_form.addRow("상태", status)
            if self._drive_disconnector is not None:
                disconnect = QPushButton("연결 해제")
                disconnect.clicked.connect(self._drive_disconnector)
                external_form.addRow("계정", disconnect)
        external_form.addRow(QLabel("API 키와 계정 인증만 이 탭에서 관리합니다."))
        external_tab.setMinimumSize(0, 0)
        tabs.addTab(external_tab, "연결")

        manage_tab = QWidget(); manage_form = QFormLayout(manage_tab)
        # 이 두 탭은 별도 창을 키우지 않고, 현재 기본설정 창 안에서만
        # 배치되어야 한다. QFormLayout의 내용 기반 최소 크기 전파를 끈다.
        manage_form.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        if self._log_opener is not None:
            manage_form.addRow(self._section_title("로그"))
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
        if self._journal_backup_exporter is not None:
            journal_backup = QPushButton("매매일지 백업 저장")
            journal_backup.clicked.connect(self._journal_backup_exporter)
            manage_form.addRow("매매일지", journal_backup)
        if self._journal_backup_importer is not None:
            journal_restore = QPushButton("매매일지 백업 불러오기")
            journal_restore.clicked.connect(self._journal_backup_importer)
            manage_form.addRow("매매일지 복원", journal_restore)
        if self._drive_connector is not None:
            manage_form.addRow(self._section_separator())
            manage_form.addRow(self._section_title("Google Drive 동기화 설정"))
            manage_form.addRow(QLabel("테마와 일반 설정을 내 드라이브의 ‘키움 실시간 모니터’ 폴더에 동기화합니다."))
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
        manage_tab.setMinimumSize(0, 0)
        tabs.addTab(manage_tab, "관리")

        info_tab = QWidget()
        info_layout = QVBoxLayout(info_tab)
        info_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        if self._update_checker is not None:
            info_layout.addWidget(self._section_title("업데이트"))
            update_check = QPushButton("업데이트 확인")
            update_check.clicked.connect(self._update_checker)
            info_layout.addWidget(update_check, alignment=Qt.AlignmentFlag.AlignHCenter)
            info_layout.addWidget(self._auto_update_check, alignment=Qt.AlignmentFlag.AlignHCenter)
            info_layout.addWidget(self._section_separator())
        info_layout.addWidget(self._section_title("정보"))
        app_info = QLabel(
            f"{APP_DISPLAY_NAME} {APP_VERSION}\n"
            f"{APP_COPYRIGHT}\n"
            "이 프로그램은 여러 오픈소스 소프트웨어를 기반으로 제작되었습니다."
        )
        app_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_info.setWordWrap(False)
        app_info.setStyleSheet("color: #667085; padding: 18px 8px;")
        info_layout.addWidget(app_info)
        info_layout.addStretch()
        info_tab.setMinimumSize(0, 0)
        tabs.addTab(info_tab, "정보")
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("설정 저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._apply_responsive_font()

    def minimumSizeHint(self) -> QSize:
        """탭의 긴 내용이 Windows 창 크기 제한으로 전파되는 것을 막는다."""
        return QSize(480, 360)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_responsive_font()
        if hasattr(self, "_dialog_size_save_timer"):
            self._dialog_size_save_timer.start()

    def _apply_responsive_font(self) -> None:
        """Keep the settings dialog readable while allowing a modest size change on resize."""
        scale = min(self.width() / 680, self.height() / 650)
        point_size = max(7, min(14, round(10 * scale)))
        if getattr(self, "_responsive_font_point_size", None) == point_size:
            return
        self._responsive_font_point_size = point_size
        self.setStyleSheet(f"QWidget {{ font-size: {point_size}pt; }}")

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
        self._high_distance_period.setCurrentIndex(HIGH_PERIODS.index(DEFAULT_SETTINGS["high_distance_period"]))
        default_cycle = selected_high_cycle_periods(DEFAULT_SETTINGS["high_header_cycle_periods"])
        for period, checkbox in self._high_header_cycle_checks.items():
            checkbox.setChecked(period in default_cycle)
        for level, field in self._near_high_fields.items():
            field.setText(DEFAULT_SETTINGS[f"near_high_{level}_percent"])
        self._near_high_row_alert_level.setCurrentIndex(("interest", "caution", "fire").index(DEFAULT_SETTINGS["near_high_row_alert_level"]))
        self._near_high_icons.setChecked(DEFAULT_SETTINGS["near_high_show_icon"] == "1")
        for level, field in self._near_high_icon_fields.items():
            field.setText(DEFAULT_SETTINGS[f"near_high_icon_{level}"])
            self._near_high_icon_images[level] = ""
            self._update_near_high_icon_image_label(level)
        self._near_high_sounds.setChecked(DEFAULT_SETTINGS["near_high_sound_enabled"] == "1")
        self._near_high_sound_cooldown.setValue(int(DEFAULT_SETTINGS["near_high_sound_cooldown_seconds"]))
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
        self._theme_group_sort_basis.setCurrentIndex(0 if DEFAULT_SETTINGS["theme_group_sort_basis"] == "all" else 1)
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

    def _open_api_settings(self, section: str = "kiwoom") -> None:
        if self._api_path is None:
            return
        dialog = ApiSettingsDialog(
            self._api_path, self, section=section,
            initial_mode=str(self._data_source_mode.currentData()),
        )
        if dialog.exec():
            if section == "kiwoom":
                LocalApiConfig(self._api_path).save_profiles(dialog.values)
            elif self._data_source_path is not None:
                self._data_source = DataSourceConfig(self._data_source_path).load()
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
        valid_row_height = row_height == 0 or 12 <= row_height <= 100
        if not (0 <= font_size <= 30 and valid_row_height and 0 <= badge_font_size <= 30 and 0 <= badge_padding <= 20 and all(value >= 0 for value in market_cap_highlights.values()) and market_cap_active_thresholds == sorted(market_cap_active_thresholds)):
            QMessageBox.warning(self, "입력 확인", "표 행 높이는 0(자동) 또는 12~100으로 입력하세요.")
            return
        cycle_periods = tuple(period for period in HIGH_PERIODS if self._high_header_cycle_checks[period].isChecked())
        if not cycle_periods:
            QMessageBox.warning(self, "입력 확인", "헤더 클릭 순환 신고가를 하나 이상 선택하세요.")
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
        self._settings.set("near_high_sound_cooldown_seconds", str(self._near_high_sound_cooldown.value()))
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
        self._settings.set("theme_group_sort_basis", str(self._theme_group_sort_basis.currentData()))
        self._settings.set("theme_trade_summary_excluded_stocks", self._theme_trade_summary_excluded_stocks.text().strip())
        self._settings.set("theme_trade_summary_excluded_enabled", "1" if self._theme_trade_summary_excluded_enabled.isChecked() else "0")
        self._settings.set("google_drive_auto_download", "1" if self._google_drive_auto_download.isChecked() else "0")
        self._settings.set("google_drive_auto_upload", "1" if self._google_drive_auto_upload.isChecked() else "0")
        self._settings.set("google_drive_auto_upload_on_exit", "1" if self._google_drive_auto_upload_on_exit.isChecked() else "0")
        self._settings.set("google_drive_sync_target", str(self._google_drive_sync_target.currentData()))
        self._settings.set("auto_update_check", "1" if self._auto_update_check.isChecked() else "0")
        if self._data_source_path is not None:
            selected_mode = str(self._data_source_mode.currentData())
            if selected_mode != self._data_source.mode:
                source = DataSourceSettings(
                    selected_mode, self._data_source.server_url, self._data_source.access_token,
                    self._data_source.local_fallback_enabled,
                    self._data_source.parallel_validation_enabled,
                )
                try:
                    source.validate()
                except ValueError as error:
                    QMessageBox.warning(self, "데이터 연결 방식", str(error))
                    return
                DataSourceConfig(self._data_source_path).save(source)
                self._data_source = source
                self.api_changed = True
        current_high_period = str(self._high_distance_period.currentData())
        if current_high_period not in cycle_periods:
            current_high_period = cycle_periods[0]
        self._settings.set("high_header_cycle_periods", ",".join(cycle_periods))
        self._settings.set("high_distance_period", current_high_period)
        self.accept()
