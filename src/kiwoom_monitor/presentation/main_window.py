from __future__ import annotations

import logging
import sys
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from PySide6.QtGui import QCloseEvent, QResizeEvent, QColor
from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QColorDialog,
    QCheckBox,
    QLineEdit,
    QInputDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFileDialog,
    QMessageBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.minute_history_worker import MinuteHistoryWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.fundamentals_worker import FundamentalsWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.new_high_worker import NewHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.daily_high_worker import DailyHighWorker
from kiwoom_monitor.application.daily_high_service import DailyHighTargets
from kiwoom_monitor.application.trade_strength import StockFundamentals, trade_strength_percent
from kiwoom_monitor.infrastructure.excel.theme_repository import ThemeRepository
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting, ColumnSettingsRepository
from kiwoom_monitor.infrastructure.excel.theme_repository import ThemeRepository as ExcelThemeRepository
from kiwoom_monitor.domain.theme_import import validate_theme_rows
from kiwoom_monitor.domain.theme_parser import parse_themes
from kiwoom_monitor.presentation.theme_colors import text_color
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import ApiProfiles, LocalApiConfig
from kiwoom_monitor.domain.strength_level import strength_badge
from kiwoom_monitor.application.theme_matching import MatchedThemeRow, match_theme_rows
from kiwoom_monitor.application.theme_preview import preview_theme_changes
from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator


class RankingLoader(Protocol):
    def load_top_stocks(self) -> tuple[object, ...]: ...


logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("기본 설정")

        self._refresh_interval = QComboBox()
        self._refresh_interval.addItems(["10", "20", "30", "60"])
        self._refresh_interval.setEditable(True)
        self._refresh_interval.setCurrentText(settings.get("refresh_interval_seconds"))
        self._ui_mode = QComboBox()
        self._ui_mode.addItem("반응형 UI", "responsive")
        self._ui_mode.addItem("고정 UI / 공간 확장", "fixed")
        self._ui_mode.setCurrentIndex(0 if settings.get("ui_mode") == "responsive" else 1)
        self._interest=QLineEdit(settings.get("strength_interest")); self._caution=QLineEdit(settings.get("strength_caution")); self._fire=QLineEdit(settings.get("strength_fire"))
        self._near_high=QLineEdit(settings.get("near_high_threshold_percent"))
        self._theme_separators=QLineEdit(settings.get("theme_custom_separators"))
        self._theme_separators.setPlaceholderText("기본 , / | ; 외에 추가할 문자")
        self._font_size=QLineEdit(settings.get("ui_font_size")); self._font_size.setPlaceholderText("0: 자동")
        self._row_height=QLineEdit(settings.get("ui_row_height")); self._row_height.setPlaceholderText("0: 자동")
        self._badge_font_size=QLineEdit(settings.get("theme_badge_font_size")); self._badge_font_size.setPlaceholderText("0: 자동")
        self._badge_padding=QLineEdit(settings.get("theme_badge_padding"))
        self._near_high_enabled=QCheckBox("신고가 근접 강조 사용")
        self._near_high_enabled.setChecked(settings.get("near_high_alert_enabled") == "1")
        self._strength_icons=QCheckBox("거래강도 단계 아이콘 표시")
        self._strength_icons.setChecked(settings.get("strength_show_icon") == "1")
        self._high_distance_period=QComboBox(); self._high_distance_period.addItem("5일 신고가", "5"); self._high_distance_period.addItem("20일 신고가", "20"); self._high_distance_period.addItem("250일 신고가(52주 근사)", "250")
        saved_period=settings.get("high_distance_period"); self._high_distance_period.setCurrentIndex(("5", "20", "250").index(saved_period) if saved_period in ("5", "20", "250") else 2)

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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _save(self) -> None:
        try:
            interval = int(self._refresh_interval.currentText())
        except ValueError:
            interval = 0
        if not 5 <= interval <= 3600:
            QMessageBox.warning(self, "입력 확인", "새로고침 주기는 5초~3,600초 사이의 정수로 입력하세요.")
            return
        try:
            interest, caution, fire = (float(field.text()) for field in (self._interest, self._caution, self._fire))
            near_high = float(self._near_high.text())
        except ValueError:
            QMessageBox.warning(self, "입력 확인", "강도 기준과 신고가 근접 기준은 0 이상의 숫자로 입력하세요.")
            return
        if interest < 0 or caution < interest or fire < caution or near_high < 0:
            QMessageBox.warning(self, "입력 확인", "강도 기준은 관심 ≤ 주의 ≤ 불 순서의 0 이상 숫자로 입력하세요.")
            return
        try:
            font_size, row_height, badge_font_size, badge_padding = (int(field.text()) for field in (self._font_size, self._row_height, self._badge_font_size, self._badge_padding))
        except ValueError:
            QMessageBox.warning(self, "입력 확인", "화면 크기 설정은 0 이상의 정수로 입력하세요.")
            return
        if not (0 <= font_size <= 30 and 0 <= row_height <= 100 and 0 <= badge_font_size <= 30 and 0 <= badge_padding <= 20):
            QMessageBox.warning(self, "입력 확인", "화면 크기 설정 범위를 확인하세요.")
            return
        self._settings.set("refresh_interval_seconds", str(interval))
        self._settings.set("ui_mode", str(self._ui_mode.currentData()))
        self._settings.set("strength_interest", str(interest)); self._settings.set("strength_caution", str(caution)); self._settings.set("strength_fire", str(fire))
        self._settings.set("near_high_threshold_percent", str(near_high))
        self._settings.set("theme_custom_separators", self._theme_separators.text().strip())
        self._settings.set("ui_font_size", str(font_size)); self._settings.set("ui_row_height", str(row_height)); self._settings.set("theme_badge_font_size", str(badge_font_size)); self._settings.set("theme_badge_padding", str(badge_padding))
        self._settings.set("near_high_alert_enabled", "1" if self._near_high_enabled.isChecked() else "0")
        self._settings.set("strength_show_icon", "1" if self._strength_icons.isChecked() else "0")
        self._settings.set("high_distance_period", str(self._high_distance_period.currentData()))
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


class ThemePreviewDialog(QDialog):
    def __init__(self, changes: tuple[object, ...], skipped: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Excel 테마 변경 미리보기")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"적용 대상 {len(changes)}건 · 이번에 제외 {skipped}건"))
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


class ThemeColorDialog(QDialog):
    PALETTE = ("#DCE6F1", "#FFF2CC", "#E2F0D9", "#FCE4D6", "#E4DFEC", "#F4CCCC", "#D9EAD3", "#CFE2F3", "#D9D2E9", "#FCE5CD")

    def __init__(self, theme: str, current: str, parent: QWidget | None = None) -> None:
        super().__init__(parent); self.setWindowTitle("테마 색상 변경"); self._color = current
        layout=QVBoxLayout(self); layout.addWidget(QLabel(f"테마: {theme}"))
        self._stock_only=QRadioButton("이 종목만"); self._all_stocks=QRadioButton("이 테마 전체"); self._all_stocks.setChecked(True)
        layout.addWidget(self._stock_only); layout.addWidget(self._all_stocks)
        colors=QHBoxLayout(); layout.addLayout(colors)
        for color in self.PALETTE:
            button=QPushButton(); button.setFixedSize(28,28); button.setStyleSheet(f"background:{color}; border: 1px solid #888;")
            button.clicked.connect(lambda _, value=color: self._choose(value)); colors.addWidget(button)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _choose(self, color: str) -> None: self._color=color
    @property
    def color(self) -> str: return self._color
    @property
    def stock_only(self) -> bool: return self._stock_only.isChecked()

class ThemeManagerDialog(QDialog):
    def __init__(self, repository: object, parent: QWidget | None = None) -> None:
        super().__init__(parent); self._repository=repository; self.setWindowTitle("종목/테마 관리"); self.resize(560,420)
        self._search=QLineEdit(); self._search.setPlaceholderText("종목명 검색"); self._table=QTableWidget(0,2); self._table.setHorizontalHeaderLabels(("종목명","테마"))
        self._add=QPushButton("신규 종목 테마 추가"); layout=QVBoxLayout(self); layout.addWidget(self._search); layout.addWidget(self._table); layout.addWidget(self._add)
        self._add.clicked.connect(self._add_new)
        self._color = QPushButton("테마 색상 변경")
        layout.addWidget(self._color)
        self._color.clicked.connect(self._edit_theme_color)
        self._search.textChanged.connect(self._reload); self._table.cellDoubleClicked.connect(self._edit); self._rows=(); self._reload()
    def _reload(self) -> None:
        self._rows=self._repository.search(self._search.text()); self._table.setRowCount(len(self._rows))
        for index,(_,name,themes) in enumerate(self._rows):
            self._table.setItem(index,0,QTableWidgetItem(name)); self._table.setItem(index,1,QTableWidgetItem(themes))
    def _edit(self, row: int, _: int) -> None:
        code,name,themes=self._rows[row]; value,ok=QInputDialog.getText(self,"테마 수정",f"{name} 테마 (, / | ; 구분)",text=themes)
        if ok and QMessageBox.question(self,"테마 변경 확인",f"{name}\n\n기존: {themes}\n변경: {value}\n\n저장할까요?") == QMessageBox.StandardButton.Yes:
            self._repository.replace_for_stock(code, parse_themes(value)); self._reload()
    def _add_new(self) -> None:
        name,ok=QInputDialog.getText(self,"신규 종목", "종목명")
        if not ok or not name.strip(): return
        code=self._repository.find_code_by_name(name.strip())
        if not code: QMessageBox.warning(self,"종목 매칭 실패","키움 종목 DB에서 찾을 수 없습니다."); return
        themes,ok=QInputDialog.getText(self,"신규 종목", "테마 (, / | ; 구분)")
        if ok and QMessageBox.question(self,"신규 종목 추가 확인",f"{name}\n{themes}\n\n추가할까요?") == QMessageBox.StandardButton.Yes:
            self._repository.replace_for_stock(code, parse_themes(themes)); self._reload()
    def _edit_theme_color(self) -> None:
        options = self._repository.list_themes()
        if not options:
            QMessageBox.information(self, "테마 색상", "먼저 종목에 테마를 등록하세요.")
            return
        names = [name for name, _ in options]
        name, ok = QInputDialog.getItem(self, "테마 색상", "색상을 바꿀 테마", names, 0, False)
        if not ok:
            return
        current = dict(options)[name]
        color = QColorDialog.getColor(QColor(current), self, f"{name} 색상")
        if color.isValid():
            self._repository.set_color(name, color.name())


class MainWindow(QMainWindow):
    COLUMNS = (("rank","순위"),("stock","종목"),("themes","테마"),("change_rate","등락률"),("strength_1m","1분강도"),("current_price","현재가"),("trade_value_1m","1분(억)"),("trade_value_5m","5분(억)"),("trade_value_60m","60분(억)"),("trade_value_day","1일(억)"),("strength_5m","5분강도"),("strength_60m","60분강도"),("strength_day","1일강도"),("new_high","신고가"),("high_distance","신고가까지"))
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
        self._themes = themes or {}
        self._columns = columns
        self._stock_lookup = stock_lookup
        self._theme_store = theme_store
        self._realtime_worker: RealtimeTradeWorker | None = None
        self._realtime_codes: tuple[str, ...] = ()
        self._minute_history_worker: MinuteHistoryWorker | None = None
        self._fundamentals_worker: FundamentalsWorker | None = None
        self._daily_high_worker: DailyHighWorker | None = None
        self._new_high_worker: NewHighWorker | None = None
        self._initial_new_high_refresh_started = False
        self._row_by_code: dict[str, int] = {}
        self._current_prices: dict[str, int] = {}
        self._today_high_codes: set[str] = set()
        self._minute_history_codes: set[str] = set()
        self._minute_aggregator = minute_aggregator or MinuteTradeValueAggregator()
        self._ranking_timer = QTimer(self)
        self._ranking_timer.timeout.connect(self._refresh_rankings)
        self.setWindowTitle("키움 실시간 종목 모니터")
        self.resize(1160, 720)

        toolbar = QToolBar("도구")
        toolbar.setMovable(False)
        self._refresh_button = QPushButton("새로고침")
        self._refresh_button.clicked.connect(self._refresh_rankings)
        self._refresh_button.setEnabled(ranking_loader is not None)
        toolbar.addWidget(self._refresh_button)
        self._new_high_button = QPushButton("신고가 갱신")
        self._new_high_button.clicked.connect(self._refresh_new_highs)
        self._new_high_button.setEnabled(ranking_loader is not None)
        toolbar.addWidget(self._new_high_button)
        self._excel_button = QPushButton("Excel 업데이트")
        self._excel_button.clicked.connect(self._select_excel)
        toolbar.addWidget(self._excel_button)
        self._theme_manager_button=QPushButton("종목/테마 관리")
        self._theme_manager_button.clicked.connect(self._open_theme_manager)
        self._theme_manager_button.setEnabled(theme_store is not None)
        toolbar.addWidget(self._theme_manager_button)
        settings_button = QPushButton("기본 설정")
        settings_button.clicked.connect(self._open_settings)
        toolbar.addWidget(settings_button)
        self._api_button=QPushButton("API 설정")
        self._api_button.clicked.connect(self._open_api_settings)
        toolbar.addWidget(self._api_button)
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
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionsMovable(True)
        self._table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.horizontalHeader().customContextMenuRequested.connect(self._show_column_menu)
        self._table.horizontalHeader().sectionMoved.connect(lambda *_: self._save_columns())
        self._table.horizontalHeader().sectionResized.connect(lambda *_: self._save_columns())
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
            interval = int(settings.get("refresh_interval_seconds")) * 1000
            self._ranking_timer.start(interval)
            QTimer.singleShot(0, self._refresh_rankings)
        else:
            QTimer.singleShot(0, self._open_api_settings)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        if dialog.exec():
            interval = self._settings.get("refresh_interval_seconds")
            self._ranking_timer.start(int(interval) * 1000)
            self._apply_table_visuals()
            for code in self._row_by_code:
                self._render_high_distance(code)
            self.statusBar().showMessage(f"기본 설정 저장 완료 · 갱신 주기 {interval}초")

    def _open_theme_manager(self) -> None:
        if self._theme_store is not None: ThemeManagerDialog(self._theme_store, self).exec()

    def _set_api_status(self, text: str, color: str) -> None:
        self._api_status.setText(text)
        self._api_status.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _edit_theme_from_main_table(self, row: int, _: int) -> None:
        if self._theme_store is None or row < 0 or row >= self._table.rowCount():
            return
        stock_item = self._table.item(row, 1)
        if stock_item is None:
            return
        display = stock_item.text()
        code = display.rsplit("(", 1)[-1].rstrip(")").strip() if "(" in display else ""
        if not code:
            return
        name = display.rsplit("(", 1)[0].strip()
        before = ", ".join(self._theme_store.themes_for_stock(code))
        value, ok = QInputDialog.getText(self, "테마 수정", f"{name} 테마 (, / | ; 구분)", text=before)
        if not ok:
            return
        after = parse_themes(value)
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
                separators = ",/|;" + self._settings.get("theme_custom_separators")
                rows, errors = validate_theme_rows(ExcelThemeRepository(Path(path)).load_rows(), separators)
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
                    self.statusBar().showMessage(f"Excel 테마 업데이트 완료 · 적용 {new + changed}건")

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
        for logical, (name, _) in enumerate(self.COLUMNS):
            setting = saved.get(name)
            if setting:
                self._table.setColumnHidden(logical, not setting.visible); header.resizeSection(logical, setting.width)
        names = [name for name, _ in self.COLUMNS]
        for target, setting in enumerate(sorted(saved.values(), key=lambda s: s.position)):
            if setting.name in names:
                logical = names.index(setting.name)
                header.moveSection(header.visualIndex(logical), target)

    def _save_columns(self) -> None:
        if self._columns is None: return
        header = self._table.horizontalHeader(); settings=[]
        for logical,(name,_) in enumerate(self.COLUMNS): settings.append(ColumnSetting(name, not self._table.isColumnHidden(logical), header.visualIndex(logical), header.sectionSize(logical)))
        self._columns.save(tuple(settings))

    def _show_column_menu(self, point: object) -> None:
        menu = QMenu(self)
        selected_column = self._table.horizontalHeader().logicalIndexAt(point)
        for logical, (_, label) in enumerate(self.COLUMNS):
            action = menu.addAction(label); action.setCheckable(True); action.setChecked(not self._table.isColumnHidden(logical))
            action.toggled.connect(lambda checked, index=logical: (self._table.setColumnHidden(index, not checked), self._save_columns()))
        menu.addSeparator()
        fit = menu.addAction("전체 컬럼 자동 맞춤")
        fit.triggered.connect(lambda: (self._table.resizeColumnsToContents(), self._save_columns()))
        if selected_column >= 0:
            fit_selected = menu.addAction("선택 열 자동 맞춤")
            fit_selected.triggered.connect(lambda: (self._table.resizeColumnToContents(selected_column), self._save_columns()))
        menu.exec(self._table.horizontalHeader().mapToGlobal(point))

    def _refresh_rankings(self) -> None:
        if self._ranking_loader is None:
            return
        self._refresh_button.setEnabled(False)
        self.statusBar().showMessage("순위와 신고가를 조회하는 중입니다…")
        QApplication.processEvents()
        try:
            stocks = self._ranking_loader.load_top_stocks()
        except Exception as error:
            logger.warning("순위 조회에 실패했습니다: %s", error)
            self._set_api_status("API: 오류", "#C00000")
            self.statusBar().showMessage("조회에 실패했습니다. 네트워크와 API 설정을 확인하세요.")
            return
        finally:
            self._refresh_button.setEnabled(True)

        self._table.setRowCount(len(stocks))
        self._set_api_status("API: 연결됨", "#008000")
        self._row_by_code.clear()
        for row, stock in enumerate(stocks):
            self._row_by_code[stock.code] = row
            values = (
                str(stock.rank),
                f"{stock.name} ({stock.code})",
                " ".join(f"[ {theme.strip()} ]" for theme in self._themes.get("".join(stock.name.split()), "").split(",") if theme.strip()) or "-",
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
                self._new_high_label(stock.code, stock.new_high_label),
                "-",
            )
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
            self._table.setCellWidget(row, 2, self._theme_badges(stock.code, stock.name))
            self._table.setItem(row, 13, QTableWidgetItem(self._new_high_label(stock.code, stock.new_high_label)))
        for stock in stocks:
            current_price = self._current_prices.get(stock.code)
            row = self._row_by_code[stock.code]
            if current_price is not None:
                self._table.setItem(row, 5, QTableWidgetItem(f"{current_price:,}"))
            self._render_trade_values(stock.code)
            self._render_high_distance(stock.code)
        self.statusBar().showMessage(f"조회 완료 · {len(stocks)}개 종목 · 실시간 체결 데이터 연결 중")
        self._start_realtime_subscription(tuple(stock.code for stock in stocks))
        self._start_fundamentals_loading(tuple(stock.code for stock in stocks))
        self._start_daily_high_loading(tuple(stock.code for stock in stocks))
        self._start_minute_history_loading(tuple(stock.code for stock in stocks))
        if not self._initial_new_high_refresh_started:
            self._initial_new_high_refresh_started = True
            self._start_new_high_refresh()
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
        if code not in self._today_high_codes or label.startswith("당일"):
            return label
        return "당일" if label == "-" else f"당일, {label}"

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
        if self._ranking_loader is None or not hasattr(self._ranking_loader, "refresh_new_highs"):
            return
        if self._new_high_worker is not None and self._new_high_worker.isRunning():
            return
        self._new_high_button.setEnabled(False)
        self.statusBar().showMessage("신고가 목록을 갱신하는 중입니다…")
        worker = NewHighWorker(self._ranking_loader)
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
        if self._realtime_worker_factory is None:
            return
        if self._realtime_worker is not None and self._realtime_worker.isRunning() and codes == self._realtime_codes:
            return
        if self._realtime_worker is not None:
            self._realtime_worker.stop()
        worker = self._realtime_worker_factory(codes)
        worker.trade_received.connect(self._on_trade_tick)
        worker.status_changed.connect(self.statusBar().showMessage)
        worker.connection_failed.connect(self._on_realtime_failure)
        self._realtime_worker = worker
        self._realtime_codes = codes
        worker.start()

    def _on_trade_tick(self, tick: TradeTick) -> None:
        row = self._row_by_code.get(tick.code)
        if row is None or tick.current_price is None:
            return
        self._table.setItem(row, 5, QTableWidgetItem(f"{tick.current_price:,}"))
        if tick.change_rate is not None:
            self._table.setItem(row, 3, QTableWidgetItem(f"{tick.change_rate:+.2f}%"))
        self._current_prices[tick.code] = tick.current_price
        self._render_high_distance(tick.code)
        near_high = False
        if tick.high_price and tick.high_price > 0:
            distance = (tick.high_price - tick.current_price) / tick.high_price * 100
            near_high = self._settings.get("near_high_alert_enabled") == "1" and distance <= float(self._settings.get("near_high_threshold_percent"))
            if tick.current_price >= tick.high_price:
                self._today_high_codes.add(tick.code)
                label = self._table.item(row, 13)
                if label is not None:
                    label.setText(self._new_high_label(tick.code, label.text()))
        for column in range(self._table.columnCount()):
            item = self._table.item(row, column)
            if item:
                item.setBackground(QColor("#FDE9E7") if near_high else QColor("white"))
        bar = self._minute_aggregator.ingest(tick, datetime.now())
        if bar is not None:
            self._render_trade_values(tick.code)

    def _start_minute_history_loading(self, codes: tuple[str, ...]) -> None:
        if self._minute_history_worker_factory is None:
            return
        if self._minute_history_worker is not None and self._minute_history_worker.isRunning():
            return
        missing_codes = tuple(code for code in codes if code not in self._minute_history_codes)
        if not missing_codes:
            return
        worker = self._minute_history_worker_factory(missing_codes)
        worker.history_received.connect(self._on_history_received)
        worker.status_changed.connect(self.statusBar().showMessage)
        worker.failed.connect(self.statusBar().showMessage)
        self._minute_history_worker = worker
        worker.start()

    def _on_history_received(self, code: str, bars: object) -> None:
        if not isinstance(bars, tuple):
            return
        self._minute_aggregator.seed(code, bars)
        self._minute_history_codes.add(code)
        self._render_trade_values(code)

    def _render_high_distance(self, code: str) -> None:
        row = self._row_by_code.get(code)
        fundamentals = self._fundamentals.get(code)
        current_price = self._current_prices.get(code)
        if row is None or fundamentals is None or current_price is None:
            return
        period = self._settings.get("high_distance_period")
        daily = self._daily_highs.get(code)
        target = daily.high_5_price if daily and period == "5" else daily.high_20_price if daily and period == "20" else fundamentals.high_250_price
        if not target:
            return
        distance = max(0.0, (target - current_price) / target * 100)
        self._table.setItem(row, 14, QTableWidgetItem(f"{distance:.2f}%"))

    def _start_daily_high_loading(self, codes: tuple[str, ...]) -> None:
        if self._daily_high_worker_factory is None or (self._daily_high_worker is not None and self._daily_high_worker.isRunning()):
            return
        missing = tuple(code for code in codes if code not in self._daily_highs)
        if not missing:
            return
        worker = self._daily_high_worker_factory(missing)
        worker.received.connect(self._on_daily_high_received)
        self._daily_high_worker = worker
        worker.start()

    def _on_daily_high_received(self, code: str, targets: object) -> None:
        if isinstance(targets, DailyHighTargets):
            self._daily_highs[code] = targets
            self._render_high_distance(code)

    def _render_trade_values(self, code: str) -> None:
        row = self._row_by_code.get(code)
        if row is None:
            return
        values = (self._minute_aggregator.trade_value_eok(code, 1), self._minute_aggregator.trade_value_eok(code, 5), self._minute_aggregator.trade_value_eok(code, 60), self._minute_aggregator.today_trade_value_eok(code))
        for column, value in enumerate(values, start=6):
            self._table.setItem(row, column, QTableWidgetItem(f"{value:.2f}"))
        fundamentals = self._fundamentals.get(code)
        if fundamentals:
            for column, value in zip((4, 10, 11, 12), values):
                strength = trade_strength_percent(value, fundamentals)
                self._table.setItem(row, column, QTableWidgetItem(strength_badge(strength, float(self._settings.get("strength_interest")), float(self._settings.get("strength_caution")), float(self._settings.get("strength_fire")), self._settings.get("strength_show_icon") == "1")))

    def _start_fundamentals_loading(self, codes: tuple[str, ...]) -> None:
        if self._fundamentals_worker_factory is None:
            return
        if self._fundamentals_worker is not None and self._fundamentals_worker.isRunning():
            return
        missing_codes = tuple(code for code in codes if code not in self._fundamentals)
        if not missing_codes:
            return
        worker = self._fundamentals_worker_factory(missing_codes)
        worker.received.connect(self._on_fundamentals_received)
        worker.start()

    def _on_fundamentals_received(self, code: str, fundamentals: object) -> None:
        if isinstance(fundamentals, StockFundamentals):
            self._fundamentals[code] = fundamentals
            self._render_high_distance(code)
            if self._stock_lookup is not None and hasattr(self._stock_lookup, "update_fundamentals"):
                self._stock_lookup.update_fundamentals(code, fundamentals.market_cap, fundamentals.float_ratio)
            self._render_trade_values(code)

    def _on_realtime_failure(self, message: str) -> None:
        logger.warning("실시간 체결 연결 실패: %s", message)
        self.statusBar().showMessage("실시간 체결 연결에 실패했습니다. 새로고침으로 다시 시도하세요.")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._realtime_worker is not None:
            self._realtime_worker.stop()
        if self._minute_history_worker is not None:
            self._minute_history_worker.stop()
        if self._fundamentals_worker is not None:
            self._fundamentals_worker.stop()
        if self._daily_high_worker is not None:
            self._daily_high_worker.stop()
        if self._new_high_worker is not None:
            self._new_high_worker.stop()
        event.accept()

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

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_table_visuals()
