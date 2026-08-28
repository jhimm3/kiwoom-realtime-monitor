from __future__ import annotations

import logging
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QCloseEvent, QDesktopServices, QGuiApplication, QPainter, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
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
from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.application.news_grouping import NewsEventGroup, group_similar_news
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository, news_identity
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.article_text import fetch_article_text
from kiwoom_monitor.infrastructure.news_ai import DEFAULT_MODELS, MODEL_OPTIONS, AINewsAnalysis, analyze_article


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


class NewsSearchWorker(QThread):
    completed = Signal(str, str, object, bool)
    failed = Signal(str, str, str)

    def __init__(self, stock_code: str, stock_name: str, credentials: NaverNewsCredentials,
                 official: OfficialNewsSettings, dart_cache_path: Path, naver_since: datetime,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._credentials = credentials
        self._official = official
        self._dart_cache_path = dart_cache_path
        self._naver_since = naver_since

    def run(self) -> None:
        items: list[StockNewsItem] = []
        errors: list[str] = []
        naver_succeeded = False
        try:
            if self._credentials.client_id and self._credentials.client_secret:
                items.extend(NaverNewsClient(self._credentials).search(self._stock_name, since=self._naver_since))
                naver_succeeded = True
        except HTTPError as error:
            message = "API 인증 또는 호출 한도를 확인하세요." if error.code in {401, 403, 429} else f"네이버 뉴스 응답 오류 ({error.code})"
            errors.append(message)
        except (URLError, TimeoutError, OSError) as error:
            errors.append(f"네트워크 연결 실패: {error}")
        except (ValueError, KeyError) as error:
            errors.append(str(error))
        try:
            if self._official.dart_enabled and self._official.dart_api_key:
                items.extend(DartDisclosureClient(self._official.dart_api_key, self._dart_cache_path).search(
                    self._stock_code, self._stock_name))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as error:
            errors.append(f"DART: {error}")
        if items or not errors:
            unique = {item.original_link or item.link or item.title: item for item in items}
            self.completed.emit(self._stock_code, self._stock_name, tuple(unique.values()), naver_succeeded)
        else:
            self.failed.emit(self._stock_code, self._stock_name, " / ".join(errors))


class AINewsWorker(QThread):
    completed = Signal(object, str, str, str)
    failed = Signal(str)

    def __init__(self, items: tuple[StockNewsItem, ...], stock_name: str, settings: NewsAISettings,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items, self._stock_name, self._settings = items, stock_name, settings

    def run(self) -> None:
        try:
            article_sections: list[str] = []
            last_error: Exception | None = None
            for index, item in enumerate(self._items, start=1):
                body = ""
                for url in dict.fromkeys((item.link, item.original_link)):
                    if not url:
                        continue
                    try:
                        body = fetch_article_text(url)
                        break
                    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
                        last_error = error
                if body:
                    article_sections.append(
                        f"[기사 {index}/{len(self._items)}: {item.title}]\n{body}"
                    )
                elif item.description:
                    article_sections.append(
                        f"[기사 {index}/{len(self._items)}: {item.title} · 검색 요약만 제공됨]\n{item.description}"
                    )
            if not article_sections:
                raise ValueError(str(last_error or "기사 본문을 가져오지 못했습니다."))
            combined_body = "\n\n".join(article_sections)
            result = analyze_article(self._settings, self._stock_name, self._items[0].title, combined_body)
            model = self._settings.model.strip() or DEFAULT_MODELS[self._settings.provider]
            self.completed.emit(result, self._settings.provider, model,
                                hashlib.sha256(combined_body.encode("utf-8")).hexdigest())
        except Exception as error:  # worker boundary: show a recoverable message in the UI
            self.failed.emit(str(error))


class NaverNewsSettingsDialog(QDialog):
    def __init__(self, config: LocalNaverNewsConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._window_settings = QSettings("KiwoomMonitor", "NewsSettingsDialog")
        self.setWindowTitle("뉴스 설정")
        self.resize(570, 650)
        self.setMinimumSize(410, 340)
        geometry = self._window_settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        credentials = config.load()
        news_filter = config.load_filter()
        ai = config.load_ai()
        official = config.load_official()
        self._client_id = QLineEdit(credentials.client_id)
        self._client_secret = QLineEdit(credentials.client_secret)
        self._client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        guide = QLabel("NAVER API HUB에서 검색 API를 신청한 뒤 Client ID와 Client Secret을 입력하세요.\n키와 뉴스 필터 설정은 현재 PC에 암호화하여 저장하며 설정 백업에는 포함하지 않습니다.")
        guide.setWordWrap(True)
        link = QPushButton("NAVER API HUB 열기")
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.ncloud.com/product/applicationService/naverApi")))
        api_box = QGroupBox("네이버 뉴스 API")
        api_layout = QFormLayout(api_box)
        api_layout.addRow(guide)
        api_layout.addRow("Client ID", self._client_id)
        api_layout.addRow("Client Secret", self._client_secret)
        api_layout.addRow(link)

        self._dart_enabled = QCheckBox("DART 공시 함께 조회")
        self._dart_enabled.setChecked(official.dart_enabled)
        self._dart_key = QLineEdit(official.dart_api_key)
        self._dart_key.setEchoMode(QLineEdit.EchoMode.Password)
        dart_link = QPushButton("OpenDART API 키 발급 페이지")
        dart_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do")))
        dart_box = QGroupBox("금융감독원 DART 공시")
        dart_layout = QFormLayout(dart_box)
        dart_layout.addRow(self._dart_enabled)
        dart_layout.addRow("API 키", self._dart_key)
        dart_layout.addRow(dart_link)

        self._ai_provider = QComboBox()
        self._ai_provider.addItem("사용 안 함", "none")
        self._ai_provider.addItem("OpenAI", "openai")
        self._ai_provider.addItem("Google Gemini", "gemini")
        self._ai_provider.addItem("Anthropic Claude", "claude")
        self._ai_provider.setCurrentIndex(max(0, self._ai_provider.findData(ai.provider)))
        self._ai_key = QLineEdit(ai.api_key)
        self._ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_model = QComboBox()
        self._ai_provider.currentIndexChanged.connect(self._populate_ai_models)
        self._populate_ai_models(ai.model)
        self._ai_limit = QSpinBox()
        self._ai_limit.setRange(0, 1_000_000)
        self._ai_limit.setSpecialValueText("무제한")
        self._ai_limit.setValue(ai.daily_limit)
        self._ai_auto_recent_limit = QSpinBox()
        self._ai_auto_recent_limit.setRange(1, 1000)
        self._ai_auto_recent_limit.setValue(ai.auto_recent_limit)
        self._ai_auto = QCheckBox("새 뉴스 자동 분석")
        self._ai_auto.setChecked(ai.auto_analyze)
        ai_link = QPushButton("선택한 AI API 키 페이지 열기")
        ai_link.clicked.connect(self._open_ai_key_page)
        ai_guide = QLabel(
            "기사 본문을 읽고 요약·긍정/부정 가능성을 판정합니다. 기본은 수동 분석이며, "
            "결과는 DB에 저장됩니다. 하루 최대 분석 건수를 0으로 두면 무제한입니다."
        )
        ai_guide.setWordWrap(True)
        ai_box = QGroupBox("AI 원문 분석")
        ai_layout = QFormLayout(ai_box)
        ai_layout.addRow(ai_guide)
        ai_layout.addRow("공급자", self._ai_provider)
        ai_layout.addRow("API 키", self._ai_key)
        ai_layout.addRow("모델", self._ai_model)
        ai_layout.addRow("하루 최대 분석 건수", self._ai_limit)
        ai_layout.addRow("종목당 최신 자동 분석 건수", self._ai_auto_recent_limit)
        ai_layout.addRow(self._ai_auto)
        ai_layout.addRow(ai_link)

        self._ad_filter_enabled = QCheckBox("뉴스 광고 필터링 사용")
        self._ad_filter_enabled.setChecked(news_filter.enabled)
        self._excluded_words = QPlainTextEdit()
        self._excluded_words.setPlainText(", ".join(news_filter.excluded_words))
        self._excluded_words.setPlaceholderText("예: 광고, 체험단, 이벤트, 할인")
        self._excluded_words.setMaximumHeight(95)
        filter_guide = QLabel("기사 제목이나 요약에 제외 단어가 하나라도 있으면 목록에서 숨깁니다. 쉼표 또는 줄바꿈으로 구분하세요.")
        filter_guide.setWordWrap(True)
        filter_box = QGroupBox("광고 뉴스 필터")
        filter_layout = QVBoxLayout(filter_box)
        filter_layout.addWidget(self._ad_filter_enabled)
        filter_layout.addWidget(filter_guide)
        filter_layout.addWidget(self._excluded_words)

        self._excluded_providers = QPlainTextEdit()
        self._excluded_providers.setPlainText(", ".join(news_filter.excluded_providers))
        self._excluded_providers.setPlaceholderText("예: 연합뉴스, yna.co.kr, 특정언론사")
        self._excluded_providers.setMaximumHeight(75)
        provider_guide = QLabel("숨길 뉴스 제공처를 언론사명 또는 원문 주소의 도메인으로 입력하세요. 쉼표 또는 줄바꿈으로 구분합니다.")
        provider_guide.setWordWrap(True)
        self._provider_filter_enabled = QCheckBox("뉴스 제공처 필터링 사용")
        self._provider_filter_enabled.setChecked(news_filter.provider_filter_enabled)
        provider_box = QGroupBox("뉴스 제공처 필터")
        provider_layout = QVBoxLayout(provider_box)
        provider_layout.addWidget(self._provider_filter_enabled)
        provider_layout.addWidget(provider_guide)
        provider_layout.addWidget(self._excluded_providers)

        column_box = QGroupBox("뉴스표 표시 열")
        column_layout = QHBoxLayout(column_box)
        self._column_checks: dict[str, QCheckBox] = {}
        for key, label in (("time", "시각"), ("provider", "제공처"), ("category", "분류"),
                           ("outlook", "판단"), ("title", "제목")):
            check = QCheckBox(label)
            check.setChecked(key in news_filter.visible_columns)
            self._column_checks[key] = check
            column_layout.addWidget(check)
        column_layout.addStretch()

        color_box = QGroupBox("뉴스 판단 색상")
        color_layout = QFormLayout(color_box)
        self._outlook_color_buttons: dict[str, QPushButton] = {}
        for key, label, color in (
            ("positive", "호재", news_filter.positive_color),
            ("negative", "악재", news_filter.negative_color),
            ("mixed", "호재·악재 혼재", news_filter.mixed_color),
            ("neutral", "판단 보류", news_filter.neutral_color),
        ):
            button = QPushButton(color.upper())
            button.setProperty("selectedColor", color.upper())
            self._set_color_button_style(button, color)
            button.clicked.connect(lambda _checked=False, target=button: self._choose_outlook_color(target))
            self._outlook_color_buttons[key] = button
            color_layout.addRow(label, button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.addWidget(api_box)
        content_layout.addWidget(dart_box)
        content_layout.addWidget(ai_box)
        content_layout.addWidget(filter_box)
        content_layout.addWidget(provider_box)
        content_layout.addWidget(column_box)
        content_layout.addWidget(color_box)
        content_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)

    def _open_ai_key_page(self) -> None:
        pages = {
            "openai": "https://platform.openai.com/api-keys",
            "gemini": "https://aistudio.google.com/app/apikey",
            "claude": "https://console.anthropic.com/settings/keys",
        }
        url = pages.get(str(self._ai_provider.currentData()))
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _choose_outlook_color(self, button: QPushButton) -> None:
        current = QColor(str(button.property("selectedColor") or "#666666"))
        selected = QColorDialog.getColor(current, self, "뉴스 판단 색상 선택")
        if selected.isValid():
            value = selected.name().upper()
            button.setProperty("selectedColor", value)
            button.setText(value)
            self._set_color_button_style(button, value)

    @staticmethod
    def _set_color_button_style(button: QPushButton, color: str) -> None:
        button.setStyleSheet(f"color: {color}; font-weight: 700;")

    def _populate_ai_models(self, saved_model: object = None) -> None:
        provider = str(self._ai_provider.currentData())
        # 초기 로드에서는 저장값을 유지하고, 사용자가 공급자를
        # 바꾸면 새 공급자의 추천 모델을 바로 선택한다.
        target = (saved_model or DEFAULT_MODELS.get(provider, "")) if isinstance(saved_model, str) \
            else DEFAULT_MODELS.get(provider, "")
        self._ai_model.clear()
        if provider == "none":
            self._ai_model.addItem("공급자를 먼저 선택하세요", "")
            self._ai_model.setEnabled(False)
            return
        self._ai_model.setEnabled(True)
        for label, model_id in MODEL_OPTIONS.get(provider, ()):
            self._ai_model.addItem(label, model_id)
        index = self._ai_model.findData(target)
        if index < 0 and target:
            self._ai_model.addItem(f"기존 저장 모델 · {target}", target)
            index = self._ai_model.count() - 1
        self._ai_model.setCurrentIndex(max(0, index))

    def _save(self) -> None:
        credentials = NaverNewsCredentials(self._client_id.text().strip(), self._client_secret.text().strip())
        if bool(credentials.client_id) != bool(credentials.client_secret):
            QMessageBox.warning(self, "입력 확인", "Client ID와 Client Secret은 둘 다 입력하거나 둘 다 비워야 합니다.")
            return
        words = tuple(dict.fromkeys(
            word.strip() for word in self._excluded_words.toPlainText().replace("\n", ",").split(",") if word.strip()
        ))
        providers = tuple(dict.fromkeys(
            provider.strip() for provider in self._excluded_providers.toPlainText().replace("\n", ",").split(",") if provider.strip()
        ))
        ai_provider = str(self._ai_provider.currentData())
        if ai_provider != "none" and not self._ai_key.text().strip():
            QMessageBox.warning(self, "입력 확인", "AI를 사용하려면 선택한 공급자의 API 키를 입력하세요.")
            return
        visible_columns = tuple(key for key, check in self._column_checks.items() if check.isChecked())
        if not visible_columns:
            QMessageBox.warning(self, "입력 확인", "뉴스표에는 한 개 이상의 열을 표시해야 합니다.")
            return
        self._config.save(credentials, NewsFilterSettings(
            self._ad_filter_enabled.isChecked(), words, providers, self._provider_filter_enabled.isChecked(),
            visible_columns,
            str(self._outlook_color_buttons["positive"].property("selectedColor")),
            str(self._outlook_color_buttons["negative"].property("selectedColor")),
            str(self._outlook_color_buttons["mixed"].property("selectedColor")),
            str(self._outlook_color_buttons["neutral"].property("selectedColor")),
        ), NewsAISettings(ai_provider, self._ai_key.text().strip(), str(self._ai_model.currentData() or ""),
                          self._ai_limit.value(), self._ai_auto_recent_limit.value(), self._ai_auto.isChecked()),
           OfficialNewsSettings(self._dart_key.text().strip(), self._dart_enabled.isChecked()))
        self.accept()

    def done(self, result: int) -> None:
        """저장·취소·X 버튼 어떤 방식으로 닫더라도 마지막 크기를 기억한다."""
        self._window_settings.setValue("geometry", self.saveGeometry())
        super().done(result)


class StockNewsWindow(QDialog):
    CHECK_INTERVAL_SECONDS = 180.0
    AUTO_REFRESH_MS = 180_000

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
        self._repository = StockNewsRepository(database_path)
        self._ai_repository = NewsAIRepository(database_path)
        self._news_filter = NewsFilterSettings()
        try:
            self._news_filter = self._config.load_filter()
        except (OSError, ValueError):
            logger.warning("저장된 뉴스 필터 설정을 읽지 못해 기본값을 사용합니다.", exc_info=True)
        self._stock_code = ""
        self._stock_name = ""
        self._items: tuple[StockNewsItem, ...] = ()
        self._visible_items: tuple[StockNewsItem, ...] = ()
        self._visible_groups: tuple[NewsEventGroup, ...] = ()
        self._selected_news_cell: tuple[int, int] | None = None
        self._worker: NewsSearchWorker | None = None
        self._settings_dialog: NaverNewsSettingsDialog | None = None
        self._ai_worker: AINewsWorker | None = None
        self._ai_item: StockNewsItem | None = None
        self._ai_stock_code = ""
        self._ai_continue = False
        self._ai_automatic_run = False
        self._auto_ai_identities: set[str] = set()
        self._pending_refresh = False
        self._auto_refresh = QTimer(self)
        self._auto_refresh.setInterval(self.AUTO_REFRESH_MS)
        self._auto_refresh.timeout.connect(self.refresh)

        self._stock_label = QLabel("메인 표에서 종목명을 클릭하세요.")
        self._stock_label.setStyleSheet("font-size: 17px; font-weight: 700;")
        self._status_label = QLabel("대기")
        self._status_label.setStyleSheet("color: #667085;")
        self._show_low_relevance = QCheckBox("관련성 낮은 뉴스도 보기")
        self._show_low_relevance.toggled.connect(self._render_items)
        refresh = QPushButton("새로고침")
        refresh.clicked.connect(lambda: self.refresh(force=True))
        settings = QPushButton("⚙")
        settings.setToolTip("뉴스 설정")
        settings.setAccessibleName("뉴스 설정")
        settings.setFixedWidth(36)
        settings.clicked.connect(self._open_settings)
        self._window_mode = QComboBox()
        self._window_mode.addItem("독립 창", "independent")
        self._window_mode.addItem("메인창에 연결", "attached")
        self._window_mode.setToolTip(
            "독립 창: 메인창과 뉴스창 중 클릭한 창이 앞으로 옵니다.\n"
            "메인창에 연결: 뉴스창이 메인창에 소속되어 메인창보다 앞에 유지됩니다."
        )
        saved_window_mode = str(self._window_settings.value("window_mode", "independent"))
        saved_window_mode_index = self._window_mode.findData(saved_window_mode)
        self._window_mode.setCurrentIndex(max(0, saved_window_mode_index))
        self._window_mode.currentIndexChanged.connect(self._change_window_mode)
        kind = QPushButton("KIND")
        kind.setToolTip("한국거래소 KIND 공시 페이지 열기")
        kind.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://kind.krx.co.kr/")))

        top = QHBoxLayout()
        top.addWidget(self._stock_label)
        top.addStretch()
        top.addWidget(self._show_low_relevance)
        top.addWidget(refresh)
        top.addWidget(kind)
        top.addWidget(self._window_mode)
        top.addWidget(settings)

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
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self._detail, 1)
        detail_buttons = QHBoxLayout(); detail_buttons.addWidget(self._ai_button); detail_buttons.addStretch(); detail_buttons.addWidget(self._open_button)
        detail_layout.addLayout(detail_buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(detail_panel)
        splitter.setSizes((390, 210))

        notice = QLabel("기본 판단은 제목·요약 규칙이고, AI 분석은 가져올 수 있는 기사 원문을 읽습니다. 모두 투자 판단을 대신하지 않으며, 원문은 기본 브라우저에서 엽니다.")
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #667085; padding: 4px;")
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._status_label)
        layout.addWidget(splitter, 1)
        layout.addWidget(notice)
        self._apply_window_mode(str(self._window_mode.currentData()), persist=False)

    def set_stock(self, code: str, name: str, *, activate: bool = True) -> None:
        changed = code != self._stock_code
        self._stock_code = code
        self._stock_name = name.strip()
        self._stock_label.setText(f"{self._stock_name} ({self._stock_code})")
        if changed:
            self._items = self._repository.load(code)
            self._render_items()
            if self._items:
                relevant_count = sum(item.assessment.relevant for item in self._items)
                self._status_label.setText(f"저장된 뉴스 {len(self._items)}건 · 증권 관련 {relevant_count}건 · 새 뉴스 확인 중")
        if not self._position_initialized:
            self._position_beside_main_window()
            self._position_initialized = True
        self.show()
        if activate:
            self.raise_()
            self.activateWindow()
        if changed:
            self.refresh()

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
        try:
            credentials = self._config.load()
        except (OSError, ValueError):
            credentials = NaverNewsCredentials()
        try:
            official = self._config.load_official()
        except (OSError, ValueError):
            official = OfficialNewsSettings()
        if (not credentials.client_id or not credentials.client_secret) and not (official.dart_enabled and official.dart_api_key):
            self._items = ()
            self._render_items()
            self._status_label.setText("뉴스 API 설정이 필요합니다.")
            return
        requested_code = self._stock_code
        requested_name = self._stock_name
        two_days_ago = datetime.now(UTC) - timedelta(days=2)
        last_naver_check = self._repository.last_naver_checked_at(requested_code)
        naver_since = max(two_days_ago, last_naver_check.astimezone(UTC)) if last_naver_check else two_days_ago
        worker = NewsSearchWorker(requested_code, requested_name, credentials, official,
                                  self._config.directory / "dart_corp_codes.json", naver_since, self)
        self._worker = worker
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._status_label.setText(f"{requested_name}의 증권 관련 뉴스를 찾는 중…")
        worker.start()

    def _on_completed(self, stock_code: str, stock_name: str, items: object, naver_succeeded: bool) -> None:
        if not isinstance(items, tuple):
            return
        known = {news_identity(item) for item in self._repository.load(stock_code)}
        new_identities = {news_identity(item) for item in items} - known
        checked_at = datetime.now(UTC)
        new_count = self._repository.upsert(
            stock_code, items, checked_at, naver_checked_at=checked_at if naver_succeeded else None,
        )
        if stock_code == self._stock_code:
            self._items = self._repository.load(stock_code)
            self._render_items()
            relevant_count = sum(item.assessment.relevant for item in self._items)
            update_text = f"새 뉴스 {new_count}건 저장" if new_count else "새 뉴스 없음 · 저장된 내용 유지"
            self._status_label.setText(f"{update_text} · 전체 {len(self._items)}건 중 증권 관련 {relevant_count}건")
            try:
                ai = self._config.load_ai()
            except (OSError, ValueError):
                ai = NewsAISettings()
            if ai.auto_analyze:
                candidates = [
                    news_identity(group.representative) for group in self._visible_groups
                    if any(news_identity(item) in new_identities for item in group.items)
                    and (group.representative.link or group.representative.original_link)
                    and self._ai_repository.load(stock_code, group.representative) is None
                ]
                self._auto_ai_identities = set(candidates[:ai.auto_recent_limit])

    def _on_failed(self, stock_code: str, stock_name: str, message: str) -> None:
        if stock_code == self._stock_code:
            suffix = " · 저장된 뉴스를 표시합니다." if self._items else ""
            self._status_label.setText(message + suffix)

    def _on_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        if self._pending_refresh:
            self._pending_refresh = False
            self.refresh(force=True)
            return
        # completed 신호는 QThread가 완전히 끝나기 직전에 전달된다. finished까지
        # 기다려 네이버의 모든 페이지와 DART 조회가 종료된 뒤 AI를 시작한다.
        if self._auto_ai_identities:
            QTimer.singleShot(0, self._auto_analyze_next)

    def _render_items(self) -> None:
        try:
            self._news_filter = self._config.load_filter()
        except (OSError, ValueError):
            logger.warning("저장된 뉴스 필터 설정을 읽지 못해 현재값을 유지합니다.", exc_info=True)
        self._apply_column_visibility()
        reassessed_items = tuple(
            StockNewsItem(
                item.title, item.description, item.link, item.original_link, item.published_at,
                assess_stock_news(self._stock_name, item.title, item.description),
            )
            for item in self._items
        )
        self._items = reassessed_items
        filtered_items = tuple(
            item for item in reassessed_items
            if not is_excluded_news(item, self._news_filter)
            and (item.assessment.relevant or self._show_low_relevance.isChecked())
        )
        self._visible_groups = group_similar_news(filtered_items)
        self._visible_items = tuple(group.representative for group in self._visible_groups)
        self._table.setRowCount(len(self._visible_items))
        self._selected_news_cell = None
        delegate = self._table.itemDelegate()
        if isinstance(delegate, NewsCellMarkerDelegate):
            delegate.set_selected_cell(None)
        self._detail.clear()
        self._open_button.setEnabled(False)
        self._ai_button.setEnabled(False)
        for row, item in enumerate(self._visible_items):
            published = item.published_at.astimezone().strftime("%m-%d %H:%M") if item.published_at else "-"
            category, outlook, _reason, source = self._effective_judgment(item)
            displayed_outlook = f"{outlook}  ᴬᴵ" if source.startswith("AI 원문 분석") else outlook
            group = self._visible_groups[row]
            suffixes = []
            if len(group.items) > 1:
                suffixes.append(f"관련 기사 {len(group.items)}건")
            if group.stage:
                suffixes.append(f"단계: {group.stage}")
            if group.past_event_republication:
                suffixes.append("과거 사건 재언급 가능")
            displayed_title = item.title + (f"  · {' · '.join(suffixes)}" if suffixes else "")
            values = (published, news_provider(item), category, displayed_outlook, displayed_title)
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 3:
                    cell.setForeground(_outlook_color(outlook, self._news_filter))
                    font = cell.font(); font.setBold(True); cell.setFont(font)
                self._table.setItem(row, column, cell)

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
                column, QHeaderView.ResizeMode.Stretch if column == last_visible else QHeaderView.ResizeMode.ResizeToContents
            )

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
        try:
            ai = self._config.load_ai()
        except (OSError, ValueError):
            ai = NewsAISettings()
        self._ai_button.setEnabled(bool((item.link or item.original_link) and ai.provider != "none" and ai.api_key)
                                   and (self._ai_worker is None or not self._ai_worker.isRunning()))

    @staticmethod
    def _related_articles_html(group: NewsEventGroup) -> str:
        if len(group.items) <= 1:
            return ""
        rows: list[str] = []
        for item in group.items[1:]:
            published = item.published_at.astimezone().strftime("%m-%d %H:%M") if item.published_at else "-"
            url = item.original_link or item.link
            title = _html(item.title)
            title_html = f"<a href='{_html(url)}'>{title}</a>" if url else title
            rows.append(f"<li>{_html(published)} · {_html(news_provider(item))} · {title_html}</li>")
        return f"<hr><p><b>관련 기사 {len(group.items)}건</b></p><ul>{''.join(rows)}</ul>"

    def _effective_judgment(self, item: StockNewsItem) -> tuple[str, str, str, str]:
        stored = self._ai_repository.load(self._stock_code, item)
        if stored is None:
            return item.assessment.category, item.assessment.outlook, item.assessment.reason, "제목·검색 요약 규칙"
        result = stored.analysis
        if result.outlook == "긍정":
            outlook = "호재 가능성 높음" if result.confidence >= 70 else "호재 가능성"
        elif result.outlook == "부정":
            outlook = "악재 가능성 높음" if result.confidence >= 70 else "악재 가능성"
        elif result.outlook == "혼재":
            outlook = "호재·악재 혼재"
        else:
            outlook = "판단 보류"
        reason = result.reason or "AI 원문 분석에서 구체적인 판단 이유를 제공하지 않았습니다."
        category = result.category or item.assessment.category
        return category, outlook, reason, f"AI 원문 분석 ({stored.provider} · 신뢰도 {result.confidence}%)"

    def _ai_html(self, item: StockNewsItem) -> str:
        stored = self._ai_repository.load(self._stock_code, item)
        if stored is None:
            return (
                "<p style='color:#667085'><b>AI 원문 분석</b>"
                f" · 관련성 {item.assessment.relevance_score}점 · 신뢰도 - · 아직 분석하지 않음</p><hr>"
            )
        result = stored.analysis
        positive = " / ".join(result.positive_evidence) or "-"
        negative = " / ".join(result.negative_evidence) or "-"
        return (
            f"<div style='background:#EEF6FF; border:1px solid #9CC7F2; padding:10px;'>"
            f"<h3 style='margin-top:0'>AI 원문 분석 · 관련성 {item.assessment.relevance_score}점"
            f" · 신뢰도 {result.confidence}%</h3>"
            f"<p><b>판단:</b> {_html(result.outlook)}</p>"
            f"<p><b>원문 기준 분류:</b> {_html(result.category or item.assessment.category)}</p>"
            f"<p><b>이유:</b> {_html(result.reason)}</p>"
            f"<p><b>긍정 근거:</b> {_html(positive)}</p>"
            f"<p><b>부정 근거:</b> {_html(negative)}</p>"
            f"<p><b>원문 요약:</b> {_html(result.summary)}</p>"
            f"<p style='color:#667085'>{_html(stored.provider)} · {_html(stored.model)} · DB 저장됨</p></div><hr>"
        )

    def _analyze_selected(self, *, automatic: bool = False) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._visible_items) or self._ai_worker is not None:
            return
        self._start_ai_analysis(self._visible_groups[row], automatic=automatic)

    def _start_ai_analysis(self, group: NewsEventGroup, *, automatic: bool) -> None:
        if self._ai_worker is not None:
            return
        try:
            settings = self._config.load_ai()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "AI 분석", str(error)); return
        used = self._ai_repository.daily_count()
        if settings.daily_limit > 0 and used >= settings.daily_limit:
            QMessageBox.information(self, "AI 분석", f"오늘 설정한 상한 {settings.daily_limit}건을 모두 사용했습니다."); return
        self._ai_item = group.representative
        self._ai_stock_code = self._stock_code
        self._ai_continue = False
        self._ai_automatic_run = automatic
        self._ai_worker = AINewsWorker(group.items, self._stock_name, settings, self)
        self._ai_worker.completed.connect(self._on_ai_completed)
        self._ai_worker.failed.connect(self._on_ai_failed)
        self._ai_worker.finished.connect(self._on_ai_finished)
        self._ai_button.setEnabled(False)
        self._ai_button.setText("AI 분석 중…")
        progress = f"{used + 1}/{settings.daily_limit}" if settings.daily_limit > 0 else f"{used + 1}/무제한"
        self._status_label.setText(f"AI가 사건 묶음 {len(group.items)}건을 읽고 있습니다… ({progress})")
        self._ai_worker.start()

    def _auto_analyze_next(self) -> None:
        if (self._worker is not None and self._worker.isRunning()) \
                or self._ai_worker is not None or not self._auto_ai_identities:
            return
        try:
            settings = self._config.load_ai()
        except (OSError, ValueError):
            return
        if not settings.auto_analyze or settings.provider == "none" or not settings.api_key:
            return
        if settings.daily_limit > 0 and self._ai_repository.daily_count() >= settings.daily_limit:
            return
        for row, item in enumerate(self._visible_items):
            identity = news_identity(item)
            if identity not in self._auto_ai_identities:
                continue
            if self._ai_repository.load(self._stock_code, item) is None and (item.link or item.original_link):
                self._auto_ai_identities.discard(identity)
                self._start_ai_analysis(self._visible_groups[row], automatic=True)
                return
        self._auto_ai_identities.clear()

    def _on_ai_completed(self, result: object, provider: str, model: str, body_hash: str) -> None:
        if isinstance(result, AINewsAnalysis) and self._ai_item is not None:
            self._ai_repository.save(self._ai_stock_code, self._ai_item, provider, model, body_hash, result)
            self._status_label.setText("AI 원문 분석을 DB에 저장했습니다.")
            self._ai_continue = self._ai_automatic_run and bool(self._auto_ai_identities)
            if self._ai_stock_code == self._stock_code:
                analyzed_identity = news_identity(self._ai_item)
                row = next((index for index, item in enumerate(self._visible_items)
                            if news_identity(item) == analyzed_identity), -1)
                if 0 <= row < len(self._visible_items):
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

    def _on_ai_failed(self, message: str) -> None:
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
        if worker is not None:
            worker.deleteLater()
        self._ai_button.setText("AI 원문 분석")
        self._ai_button.setEnabled(self._table.currentRow() >= 0)
        self._ai_automatic_run = False
        if self._ai_continue:
            QTimer.singleShot(0, self._auto_analyze_next)

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
            dialog = NaverNewsSettingsDialog(self._config, self)
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
        self._render_items()
        self.refresh(force=True)

    def _clear_settings_dialog(self, dialog: NaverNewsSettingsDialog) -> None:
        if self._settings_dialog is dialog:
            self._settings_dialog = None
        dialog.deleteLater()

    def _change_window_mode(self) -> None:
        self._apply_window_mode(str(self._window_mode.currentData()))

    def _apply_window_mode(self, mode: str, *, persist: bool = True) -> None:
        """뉴스창을 독립 창 또는 메인창 소유 창으로 즉시 전환한다."""
        mode = "attached" if mode == "attached" and self._main_window is not None else "independent"
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
    from html import escape
    return escape(value).replace("\n", "<br>")
