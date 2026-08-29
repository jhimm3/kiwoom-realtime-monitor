from __future__ import annotations

import logging
import hashlib
import sqlite3
from dataclasses import replace
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
from kiwoom_monitor.application.news_grouping import NewsEventGroup, group_similar_news
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import (
    NewsAIRepository,
    StoredAINewsAnalysis,
    news_identity,
)
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.article_text import fetch_article_text
from kiwoom_monitor.infrastructure.news_ai import (
    DEFAULT_MODELS, MODEL_OPTIONS, AINewsAnalysis, AIRequestUsage, analyze_articles,
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


class NewsSearchWorker(QThread):
    completed = Signal(str, str, object, bool, object, int)
    failed = Signal(str, str, str)

    def __init__(self, stock_code: str, stock_name: str, credentials: NaverNewsCredentials,
                 official: OfficialNewsSettings, dart_cache_path: Path, naver_since: datetime,
                 database_path: Path,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._credentials = credentials
        self._official = official
        self._dart_cache_path = dart_cache_path
        self._naver_since = naver_since
        self._database_path = database_path

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
            fetched = tuple(unique.values())
            try:
                repository = StockNewsRepository(self._database_path)
                known = {news_identity(item) for item in repository.load(self._stock_code)}
                new_identities = {news_identity(item) for item in fetched} - known
                checked_at = datetime.now(UTC)
                new_count = repository.upsert(
                    self._stock_code, fetched, checked_at,
                    naver_checked_at=checked_at if naver_succeeded else None,
                )
            except (OSError, ValueError, sqlite3.Error) as error:
                self.failed.emit(self._stock_code, self._stock_name, f"뉴스 저장 실패: {error}")
                return
            self.completed.emit(
                self._stock_code, self._stock_name, fetched, naver_succeeded,
                new_identities, new_count,
            )
        else:
            self.failed.emit(self._stock_code, self._stock_name, " / ".join(errors))


class NewsPrepareWorker(QThread):
    """DB 조회와 사건 묶음을 UI 스레드 밖에서 준비한다."""

    completed = Signal(int, str, object, object, object, bool, object)
    failed = Signal(int, str, str)

    def __init__(self, request_id: int, stock_code: str, stock_name: str, database_path: Path,
                 news_filter: NewsFilterSettings, show_low_relevance: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._request_id = request_id
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._database_path = database_path
        self._news_filter = news_filter
        self._show_low_relevance = show_low_relevance

    def run(self) -> None:
        try:
            # 기본 관련성·분류·판단은 저장 당시 계산된 값을 그대로 사용한다.
            repository = StockNewsRepository(self._database_path)
            items = repository.load(self._stock_code)
            filtered = tuple(
                item for item in items
                if not is_excluded_news(item, self._news_filter)
                and (item.assessment.relevant or self._show_low_relevance)
            )
            groups = group_similar_news(filtered)
            representatives = tuple(group.representative for group in groups)
            ai_results = NewsAIRepository(self._database_path).load_many(
                self._stock_code, representatives, self._stock_name,
            )
            recently_checked = repository.recently_checked(self._stock_code, StockNewsWindow.CHECK_INTERVAL_SECONDS)
            last_naver_check = repository.last_naver_checked_at(self._stock_code)
            self.completed.emit(
                self._request_id, self._stock_code, items, groups, ai_results,
                recently_checked, last_naver_check,
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            self.failed.emit(self._request_id, self._stock_code, str(error))


class AINewsWorker(QThread):
    completed = Signal(object, object, str, str, object, int)
    failed = Signal(str, bool, int)

    def __init__(self, groups: tuple[NewsEventGroup, ...], stock_name: str, settings: NewsAISettings,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups, self._stock_name, self._settings = groups, stock_name, settings

    def run(self) -> None:
        api_attempted = False
        article_count = 0
        try:
            event_inputs: list[tuple[str, str]] = []
            body_hashes: list[str] = []
            for group in self._groups:
                article_sections: list[str] = []
                last_error: Exception | None = None
                for index, item in enumerate(group.items, start=1):
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
                        article_sections.append(f"[관련 기사 {index}/{len(group.items)}: {item.title}]\n{body}")
                    elif item.description:
                        article_sections.append(f"[관련 기사 {index}/{len(group.items)}: {item.title} · 검색 요약]\n{item.description}")
                if not article_sections:
                    raise ValueError(str(last_error or "기사 본문을 가져오지 못했습니다."))
                combined_body = "\n\n".join(article_sections)
                event_inputs.append((group.representative.title, combined_body))
                body_hashes.append(hashlib.sha256(combined_body.encode("utf-8")).hexdigest())
                article_count += len(group.items)
            api_attempted = True
            results, usage = analyze_articles(self._settings, self._stock_name, tuple(event_inputs))
            model = self._settings.model.strip() or DEFAULT_MODELS[self._settings.provider]
            self.completed.emit(results, tuple(body_hashes), self._settings.provider, model, usage, article_count)
        except Exception as error:  # worker boundary: show a recoverable message in the UI
            self.failed.emit(str(error), api_attempted, article_count)


class NaverNewsSettingsDialog(QDialog):
    def __init__(self, config: LocalNaverNewsConfig, parent: QWidget | None = None,
                 *, database_path: Path | None = None) -> None:
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
        self._ai_request_mode = QComboBox()
        self._ai_request_mode.addItem("기사별 1건씩 요청", "single")
        self._ai_request_mode.addItem("여러 사건을 한 요청으로 묶기", "batch")
        self._ai_request_mode.setCurrentIndex(max(0, self._ai_request_mode.findData(ai.request_mode)))
        self._ai_batch_size = QSpinBox()
        self._ai_batch_size.setRange(2, 20)
        self._ai_batch_size.setValue(ai.batch_size)
        self._ai_request_mode.currentIndexChanged.connect(
            lambda: self._ai_batch_size.setEnabled(self._ai_request_mode.currentData() == "batch")
        )
        self._ai_batch_size.setEnabled(ai.request_mode == "batch")
        ai_link = QPushButton("선택한 AI API 키 페이지 열기")
        ai_link.clicked.connect(self._open_ai_key_page)
        ai_guide = QLabel(
            "기사 본문을 읽고 요약·긍정/부정 가능성을 판정합니다. 기본은 수동 분석이며, "
            "결과와 실제 API 요청 횟수·토큰 사용량은 DB에 저장됩니다. 묶음 요청은 여러 사건을 "
            "한 번 호출하므로 RPD를 절약합니다. 하루 최대 요청 건수를 0으로 두면 무제한입니다."
        )
        ai_guide.setWordWrap(True)
        usage_text = "오늘 앱 기록: 아직 API 요청 통계를 확인할 수 없습니다."
        if database_path is not None:
            requests, input_tokens, output_tokens, total_tokens = NewsAIRepository(database_path).daily_usage()
            usage_text = (
                f"오늘 앱 기록: API 요청 {requests}회 · 입력 {input_tokens:,} · "
                f"출력 {output_tokens:,} · 합계 {total_tokens:,} 토큰"
            )
        self._ai_usage = QLabel(usage_text)
        self._ai_usage.setWordWrap(True)
        self._ai_usage.setStyleSheet("color:#52606d;")
        ai_box = QGroupBox("AI 원문 분석")
        ai_layout = QFormLayout(ai_box)
        ai_layout.addRow(ai_guide)
        ai_layout.addRow(self._ai_usage)
        ai_layout.addRow("공급자", self._ai_provider)
        ai_layout.addRow("API 키", self._ai_key)
        ai_layout.addRow("모델", self._ai_model)
        ai_layout.addRow("하루 최대 API 요청 건수", self._ai_limit)
        ai_layout.addRow("종목당 최신 자동 분석 건수", self._ai_auto_recent_limit)
        ai_layout.addRow("API 요청 방식", self._ai_request_mode)
        ai_layout.addRow("묶음당 최대 사건 수", self._ai_batch_size)
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
                          self._ai_limit.value(), self._ai_auto_recent_limit.value(), self._ai_auto.isChecked(),
                          str(self._ai_request_mode.currentData()), self._ai_batch_size.value()),
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
        self._database_path = database_path
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
        self._status_label = QLabel("대기")
        self._status_label.setStyleSheet("color: #667085;")
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
        kind = QPushButton("KIND")
        kind.setToolTip("한국거래소 KIND 공시 페이지 열기")
        kind.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://kind.krx.co.kr/")))

        top = QHBoxLayout()
        top.addWidget(self._stock_label)
        top.addStretch()
        top.addWidget(self._auto_ai_toggle)
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
        self._continue_ai_button = QPushButton("선택 위치부터 미분석 이어서 분석")
        self._continue_ai_button.setToolTip("선택한 행부터 과거 방향으로 미분석 사건을 설정 건수만큼 분석합니다.")
        self._continue_ai_button.clicked.connect(self._analyze_unanalyzed_from_selection)
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self._detail, 1)
        detail_buttons = QHBoxLayout(); detail_buttons.addWidget(self._ai_button); detail_buttons.addWidget(self._continue_ai_button); detail_buttons.addStretch(); detail_buttons.addWidget(self._open_button)
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
        layout.addWidget(self._count_label)
        layout.addWidget(self._status_label)
        layout.addWidget(splitter, 1)
        layout.addWidget(notice)
        self._apply_window_mode(str(self._window_mode.currentData()), persist=False)

    def set_stock(self, code: str, name: str, *, activate: bool = True) -> None:
        changed = code != self._stock_code
        if changed:
            self._pending_new_identities.clear()
            self._auto_ai_identities.clear()
        self._stock_code = code
        self._stock_name = name.strip()
        self._stock_label.setText(f"{self._stock_name} ({self._stock_code})")
        if changed:
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
        self._visible_groups = groups
        self._visible_items = tuple(group.representative for group in groups)
        self._ai_result_cache = ai_results
        self._render_items()
        relevant_count = sum(item.assessment.relevant for item in items)
        self._count_label.setText(f"저장된 뉴스 {len(items)}건 · 증권 관련 {relevant_count}건")
        # 건수는 전용 줄에 계속 표시하므로 AI·조회 상태 줄에는 중복하지 않는다.
        self._status_label.clear()
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

    def _on_prepare_finished(self) -> None:
        worker = self._prepare_worker
        self._prepare_worker = None
        if worker is not None:
            worker.wait()
            worker.deleteLater()
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
        if (not credentials.client_id or not credentials.client_secret) and not (official.dart_enabled and official.dart_api_key):
            self._status_label.setText("뉴스 API 설정이 필요합니다. 저장된 뉴스는 그대로 표시합니다.")
            return
        requested_code = self._stock_code
        requested_name = self._stock_name
        two_days_ago = datetime.now(UTC) - timedelta(days=2)
        naver_since = max(two_days_ago, last_naver_check.astimezone(UTC)) if last_naver_check else two_days_ago
        worker = NewsSearchWorker(requested_code, requested_name, credentials, official,
                                  self._config.directory / "dart_corp_codes.json", naver_since,
                                  self._database_path, self)
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
        candidates = [
            news_identity(group.representative) for group in self._visible_groups
            if any(news_identity(item) in new_identities for item in group.items)
            and (group.representative.link or group.representative.original_link)
            and news_identity(group.representative) not in self._ai_result_cache
        ]
        self._auto_ai_identities = set(candidates[:ai.auto_recent_limit])

    def _configure_recent_auto_candidates(self) -> None:
        try:
            ai = self._config.load_ai()
        except (OSError, ValueError):
            return
        if not ai.auto_analyze:
            return
        # 중간에 다른 종목/이전 실행에서 분석된 기사가 있어도 거기서 멈추지
        # 않고, 전체 목록에서 실제 미분석 사건을 최신순 N개 찾는다.
        candidates = (
            news_identity(group.representative) for group in self._visible_groups
            if (group.representative.link or group.representative.original_link)
            and news_identity(group.representative) not in self._ai_result_cache
        )
        self._auto_ai_identities = set(tuple(candidates)[:ai.auto_recent_limit])

    def _on_failed(self, stock_code: str, stock_name: str, message: str) -> None:
        if stock_code == self._stock_code:
            suffix = " · 저장된 뉴스를 표시합니다." if self._items else ""
            self._status_label.setText(message + suffix)

    def _on_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.wait()
            worker.deleteLater()
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
        stored = self._ai_result_cache.get(news_identity(item))
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
        stored = self._ai_result_cache.get(news_identity(item))
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
        self._start_ai_groups((group,), automatic=automatic)

    def _start_ai_groups(self, groups: tuple[NewsEventGroup, ...], *, automatic: bool) -> None:
        if self._ai_worker is not None:
            return
        try:
            settings = self._config.load_ai()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "AI 분석", str(error)); return
        used = self._ai_repository.daily_count()
        if settings.daily_limit > 0 and used >= settings.daily_limit:
            QMessageBox.information(self, "AI 분석", f"오늘 설정한 상한 {settings.daily_limit}건을 모두 사용했습니다."); return
        if not groups:
            return
        self._ai_groups = groups
        self._ai_item = groups[0].representative
        self._ai_stock_code = self._stock_code
        self._ai_continue = False
        self._ai_automatic_run = automatic
        self._ai_worker = AINewsWorker(groups, self._stock_name, settings, self)
        self._ai_worker.completed.connect(self._on_ai_completed)
        self._ai_worker.failed.connect(self._on_ai_failed)
        self._ai_worker.finished.connect(self._on_ai_finished)
        self._ai_button.setEnabled(False)
        self._ai_button.setText("AI 분석 중…")
        progress = f"{used + 1}/{settings.daily_limit}" if settings.daily_limit > 0 else f"{used + 1}/무제한"
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
        candidates = tuple(
            group for group in self._visible_groups[start:]
            if news_identity(group.representative) not in self._ai_result_cache
            and (group.representative.link or group.representative.original_link)
        )[:settings.auto_recent_limit]
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
        if (not settings.auto_analyze and not self._manual_ai_queue) or settings.provider == "none" or not settings.api_key:
            return
        if settings.daily_limit > 0 and self._ai_repository.daily_count() >= settings.daily_limit:
            return
        candidates: list[NewsEventGroup] = []
        for row, item in enumerate(self._visible_items):
            identity = news_identity(item)
            if identity not in self._auto_ai_identities:
                continue
            if identity not in self._ai_result_cache and (item.link or item.original_link):
                candidates.append(self._visible_groups[row])
                if settings.request_mode == "single" or len(candidates) >= settings.batch_size:
                    break
        if candidates:
            for group in candidates:
                self._auto_ai_identities.discard(news_identity(group.representative))
            self._start_ai_groups(tuple(candidates), automatic=True)
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
        self._ai_repository.log_request(provider, model, request_mode, len(self._ai_groups), article_count, request_usage)
        for group, body_hash, result in zip(self._ai_groups, body_hashes, results, strict=True):
            if not isinstance(result, AINewsAnalysis):
                continue
            item = group.representative
            self._ai_repository.save(self._ai_stock_code, item, provider, model, str(body_hash), result)
            if self._ai_stock_code == self._stock_code:
                self._ai_result_cache[news_identity(item)] = StoredAINewsAnalysis(
                    result, provider, model, datetime.now(UTC),
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
        if worker is not None:
            worker.wait()
            worker.deleteLater()
        self._ai_button.setText("AI 원문 분석")
        self._ai_button.setEnabled(self._table.currentRow() >= 0)
        self._ai_automatic_run = False
        self._ai_groups = ()
        # 이전 작업이 끝나기 직전 또는 끝난 직후 마지막 종목의 후보가
        # 만들어지는 두 경우 모두 여기서 다시 확인한다.
        self._resume_auto_analysis()

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
            dialog = NaverNewsSettingsDialog(self._config, self, database_path=self._database_path)
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
    from html import escape
    return escape(value).replace("\n", "<br>")
