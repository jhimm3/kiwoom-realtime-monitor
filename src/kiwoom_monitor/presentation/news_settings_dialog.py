from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSettings, QUrl, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from kiwoom_monitor.infrastructure.naver_news import (
    LocalNaverNewsConfig,
    NaverNewsCredentials,
    NewsAISettings,
    NewsFilterSettings,
    OfficialNewsSettings,
)
from kiwoom_monitor.infrastructure.news_ai import DEFAULT_MODELS, MODEL_OPTIONS
from kiwoom_monitor.infrastructure.central_operational_settings import (
    CentralOperationalSettingsClient,
    apply_to_local_news,
)
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository
from kiwoom_monitor.presentation.settings_request_worker import SettingsRequestWorker

class NaverNewsSettingsDialog(QDialog):
    def __init__(self, config: LocalNaverNewsConfig, parent: QWidget | None = None,
                 *, database_path: Path | None = None, section: str = "news",
                 operational_client: CentralOperationalSettingsClient | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._operational_client = operational_client
        self._operational_error = ""
        self._operations_worker: SettingsRequestWorker | None = None
        self._operations_baseline: dict[str, object] = {}
        self._initial_load_started = False
        self._closed = False
        self._window_settings = QSettings("KiwoomMonitor", "NewsSettingsDialog")
        self._section = section if section in {"news", "connections", "all"} else "news"
        self.setWindowTitle("뉴스 API 연결" if self._section == "connections" else "뉴스 설정")
        self.resize(570, 650)
        self.setMinimumSize(410, 340)
        geometry = self._window_settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        credentials = config.load()
        news_filter = config.load_filter()
        ai = config.load_ai()
        official = config.load_official()
        shortcuts = config.load_shortcuts()
        operations: dict[str, object] = {}
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
        if self._section != "connections":
            dart_layout.addRow(self._dart_enabled)
        dart_layout.addRow("API 키", self._dart_key)
        dart_layout.addRow(dart_link)

        shortcut_guide = QLabel("이름과 주소를 입력한 항목만 뉴스창에 표시됩니다. 최대 5개까지 만들 수 있습니다.")
        shortcut_guide.setWordWrap(True)
        shortcut_box = QGroupBox("뉴스창 바로가기")
        shortcut_layout = QFormLayout(shortcut_box)
        shortcut_layout.addRow(shortcut_guide)
        self._shortcut_edits: list[tuple[QLineEdit, QLineEdit]] = []
        for index in range(5):
            name, url = shortcuts[index] if index < len(shortcuts) else ("", "")
            name_edit = QLineEdit(name)
            name_edit.setPlaceholderText("버튼 이름")
            url_edit = QLineEdit(url)
            url_edit.setPlaceholderText("https://...")
            row = QHBoxLayout()
            row.addWidget(name_edit, 1)
            row.addWidget(url_edit, 3)
            shortcut_layout.addRow(f"바로가기 {index + 1}", row)
            self._shortcut_edits.append((name_edit, url_edit))

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
        self._ai_box = ai_box
        ai_box.setParent(self)
        ai_layout = QFormLayout(ai_box)
        if self._section != "connections":
            ai_layout.addRow(ai_guide)
            ai_layout.addRow(self._ai_usage)
        ai_layout.addRow("공급자", self._ai_provider)
        ai_layout.addRow("API 키", self._ai_key)
        if self._section != "connections":
            ai_layout.addRow("모델", self._ai_model)
            ai_layout.addRow("하루 최대 API 요청 건수", self._ai_limit)
            ai_layout.addRow("종목당 최신 자동 분석 건수", self._ai_auto_recent_limit)
            ai_layout.addRow("API 요청 방식", self._ai_request_mode)
            ai_layout.addRow("묶음당 최대 사건 수", self._ai_batch_size)
            ai_layout.addRow(self._ai_auto)
        ai_layout.addRow(ai_link)
        if operational_client is not None:
            status = QLabel(
                f"NAS 설정 불러오기 실패 · {self._operational_error}"
                if self._operational_error else "NAS 운영 설정과 연동됨"
            )
            status.setWordWrap(True)
            status.setStyleSheet("color:#C00000;" if self._operational_error else "color:#008000;")
            ai_layout.addRow("NAS", status)
            self._operations_status = status
            reload_button = QPushButton("NAS 운영 설정 다시 불러오기")
            self._operations_reload_button = reload_button
            reload_button.clicked.connect(self._load_operational_settings)
            ai_layout.addRow(reload_button)

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

        raw_processing_providers = operations.get("news_processing_excluded_providers", [])
        processing_providers = raw_processing_providers if isinstance(raw_processing_providers, list) else []
        self._processing_excluded_providers = QPlainTextEdit()
        self._processing_excluded_providers.setPlainText(", ".join(
            str(value) for value in processing_providers if str(value).strip()
        ))
        self._processing_excluded_providers.setPlaceholderText("예: thebell.co.kr, 특정언론사")
        self._processing_excluded_providers.setMaximumHeight(75)
        processing_guide = QLabel(
            "NAS에는 기사 제목·링크를 남기고, 이 언론사의 새 원문 추출·규칙 분류·자동 AI 처리만 제외합니다. "
            "이미 처리됐거나 대기 중인 작업은 유지됩니다."
        )
        processing_guide.setWordWrap(True)
        processing_box = QGroupBox("NAS 뉴스 처리 제외")
        processing_box.setParent(self)
        processing_layout = QVBoxLayout(processing_box)
        processing_layout.addWidget(processing_guide)
        processing_layout.addWidget(self._processing_excluded_providers)

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
        self._buttons = buttons
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        content = QWidget()
        self._content = content
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        if self._section in {"connections", "all"}:
            content_layout.addWidget(api_box)
            content_layout.addWidget(dart_box)
            content_layout.addWidget(ai_box)
        if self._section in {"news", "all"}:
            if self._section == "news":
                dart_option_box = QGroupBox("공시 조회")
                dart_option_layout = QVBoxLayout(dart_option_box)
                dart_option_layout.addWidget(self._dart_enabled)
                content_layout.addWidget(dart_option_box)
                ai_options_box = QGroupBox("AI 원문 분석")
                ai_options_layout = QFormLayout(ai_options_box)
                ai_options_layout.addRow(ai_guide)
                ai_options_layout.addRow(self._ai_usage)
                self._current_provider_label = QLabel(self._ai_provider.currentText())
                ai_options_layout.addRow("현재 공급자", self._current_provider_label)
                if operational_client is not None:
                    ai_options_layout.addRow("NAS", self._operations_status)
                    ai_options_layout.addRow(self._operations_reload_button)
                ai_options_layout.addRow("모델", self._ai_model)
                ai_options_layout.addRow("하루 최대 API 요청 건수", self._ai_limit)
                ai_options_layout.addRow("종목당 최신 자동 분석 건수", self._ai_auto_recent_limit)
                ai_options_layout.addRow("API 요청 방식", self._ai_request_mode)
                ai_options_layout.addRow("묶음당 최대 사건 수", self._ai_batch_size)
                ai_options_layout.addRow(self._ai_auto)
                content_layout.addWidget(ai_options_box)
            content_layout.addWidget(shortcut_box)
            content_layout.addWidget(filter_box)
            content_layout.addWidget(provider_box)
            if operational_client is not None:
                content_layout.addWidget(processing_box)
            content_layout.addWidget(column_box)
            content_layout.addWidget(color_box)
        content_layout.addStretch()
        if self._section == "news":
            ai_box.hide()
        if self._section == "connections" or operational_client is None:
            processing_box.hide()
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._closed = False
        if self._operational_client is not None and not self._initial_load_started:
            self._initial_load_started = True
            self._load_operational_settings()

    def _set_operational_controls_enabled(self, enabled: bool) -> None:
        for widget in (self._ai_provider, self._ai_model, self._ai_limit,
                       self._dart_enabled, self._processing_excluded_providers):
            widget.setEnabled(enabled)
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(enabled)

    def _load_operational_settings(self) -> None:
        if self._operations_worker is not None or self._operational_client is None:
            return
        self._set_operational_controls_enabled(False)
        self._operations_status.setText("NAS 운영 설정을 불러오는 중…")
        worker = SettingsRequestWorker(self._operational_client.load)
        self._operations_worker = worker
        worker.succeeded.connect(self._operational_settings_loaded)
        worker.failed.connect(self._operational_settings_failed)
        worker.start()

    @Slot(object)
    def _operational_settings_loaded(self, values: dict[str, object]) -> None:
        self._operations_worker = None
        if self._closed:
            return
        self._operational_error = ""
        self._operations_baseline = dict(values)
        index = self._ai_provider.findData(str(values.get("ai_provider", "none")))
        self._ai_provider.setCurrentIndex(max(0, index))
        if hasattr(self, "_current_provider_label"):
            self._current_provider_label.setText(self._ai_provider.currentText())
        self._populate_ai_models(str(values.get("ai_model", "")))
        self._ai_limit.setValue(int(values.get("ai_daily_limit", 0)))
        self._dart_enabled.setChecked(bool(values.get("dart_enabled", False)))
        self._processing_excluded_providers.setPlainText(
            ", ".join(str(value) for value in values.get("news_processing_excluded_providers", []))
        )
        self._set_operational_controls_enabled(True)
        self._operations_status.setText("NAS 운영 설정과 연동됨")
        try:
            apply_to_local_news(self._config, values)
        except (ValueError, OSError):
            self._operations_status.setText("NAS 설정 수신 완료 · PC 설정 반영 실패")

    @Slot(str)
    def _operational_settings_failed(self, message: str) -> None:
        self._operations_worker = None
        if self._closed:
            return
        self._operational_error = message
        self._content.setEnabled(True)
        self._operations_status.setText(f"NAS 설정 실패 · 다시 불러오세요. {message}")
        self._set_operational_controls_enabled(True)
        # Loaded shared values cannot be edited/saved after a conflict.
        for widget in (self._ai_provider, self._ai_model, self._ai_limit,
                       self._dart_enabled, self._processing_excluded_providers):
            widget.setEnabled(False)

    @Slot(object)
    def _operational_settings_saved(self, _values: object) -> None:
        self._operations_worker = None
        if not self._closed:
            self.accept()

    def _save(self) -> None:
        if self._operations_worker is not None:
            return
        credentials = self._config.load()
        news_filter = self._config.load_filter()
        ai_settings = self._config.load_ai()
        official_settings = self._config.load_official()
        shortcuts = self._config.load_shortcuts()

        if self._section in {"connections", "all"}:
            credentials = NaverNewsCredentials(self._client_id.text().strip(), self._client_secret.text().strip())
            if bool(credentials.client_id) != bool(credentials.client_secret):
                QMessageBox.warning(self, "입력 확인", "Client ID와 Client Secret은 둘 다 입력하거나 둘 다 비워야 합니다.")
                return
            ai_provider = str(self._ai_provider.currentData())
            if ai_provider != "none" and not self._ai_key.text().strip():
                QMessageBox.warning(self, "입력 확인", "AI를 사용하려면 선택한 공급자의 API 키를 입력하세요.")
                return
            ai_settings = replace(
                ai_settings, provider=ai_provider, api_key=self._ai_key.text().strip(),
            )
            official_settings = replace(official_settings, dart_api_key=self._dart_key.text().strip())

        if self._section in {"news", "all"}:
            words = tuple(dict.fromkeys(
                word.strip() for word in self._excluded_words.toPlainText().replace("\n", ",").split(",")
                if word.strip()
            ))
            providers = tuple(dict.fromkeys(
                provider.strip() for provider in self._excluded_providers.toPlainText().replace("\n", ",").split(",")
                if provider.strip()
            ))
            visible_columns = tuple(key for key, check in self._column_checks.items() if check.isChecked())
            if not visible_columns:
                QMessageBox.warning(self, "입력 확인", "뉴스표에는 한 개 이상의 열을 표시해야 합니다.")
                return
            shortcut_values: list[tuple[str, str]] = []
            for name_edit, url_edit in self._shortcut_edits:
                name, url = name_edit.text().strip(), url_edit.text().strip()
                if not name and not url:
                    continue
                if not name or not url:
                    QMessageBox.warning(self, "입력 확인", "바로가기는 이름과 주소를 모두 입력하거나 모두 비워야 합니다.")
                    return
                parsed = QUrl(url)
                if not parsed.isValid() or parsed.scheme().lower() not in {"http", "https"}:
                    QMessageBox.warning(self, "입력 확인", f"'{name}' 바로가기 주소는 http:// 또는 https://로 시작해야 합니다.")
                    return
                shortcut_values.append((name, url))
            shortcuts = tuple(shortcut_values)
            news_filter = NewsFilterSettings(
                self._ad_filter_enabled.isChecked(), words, providers,
                self._provider_filter_enabled.isChecked(), visible_columns,
                str(self._outlook_color_buttons["positive"].property("selectedColor")),
                str(self._outlook_color_buttons["negative"].property("selectedColor")),
                str(self._outlook_color_buttons["mixed"].property("selectedColor")),
                str(self._outlook_color_buttons["neutral"].property("selectedColor")),
            )
            ai_settings = replace(
                ai_settings,
                model=str(self._ai_model.currentData() or ""),
                daily_limit=self._ai_limit.value(),
                auto_recent_limit=self._ai_auto_recent_limit.value(),
                auto_analyze=self._ai_auto.isChecked(),
                request_mode=str(self._ai_request_mode.currentData()),
                batch_size=self._ai_batch_size.value(),
            )
            official_settings = replace(official_settings, dart_enabled=self._dart_enabled.isChecked())
        try:
            self._config.save(credentials, news_filter, ai_settings, official_settings, shortcuts)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "뉴스 설정 저장", f"로컬 뉴스 설정을 저장하지 못했습니다.\n{error}")
            return
        if self._operational_client is not None and not self._operational_error:
            changes: dict[str, object] = {
                "ai_provider": ai_settings.provider,
                "ai_model": ai_settings.model,
                "ai_daily_limit": ai_settings.daily_limit,
                "dart_enabled": official_settings.dart_enabled,
            }
            if self._section in {"news", "all"}:
                changes["news_processing_excluded_providers"] = list(dict.fromkeys(
                    value.strip() for value in
                    self._processing_excluded_providers.toPlainText().replace("\n", ",").split(",")
                    if value.strip()
                ))
            changes = {name: value for name, value in changes.items()
                       if value != self._operations_baseline.get(name)}
            client, config = self._operational_client, self._config

            def save() -> object:
                values = client.update(changes)
                apply_to_local_news(config, values)
                return values

            self._set_operational_controls_enabled(False)
            self._content.setEnabled(False)
            self._operations_status.setText("NAS 운영 설정을 저장하는 중…")
            worker = SettingsRequestWorker(save)
            self._operations_worker = worker
            worker.succeeded.connect(self._operational_settings_saved)
            worker.failed.connect(self._operational_settings_failed)
            worker.start()
            return
        if self._operational_client is not None and self._operational_error:
            QMessageBox.warning(self, "NAS 뉴스 설정",
                                "PC 설정만 저장했습니다. NAS 설정은 다시 불러온 뒤 저장하세요.")
        self.accept()

    def done(self, result: int) -> None:
        """저장·취소·X 버튼 어떤 방식으로 닫더라도 마지막 크기를 기억한다."""
        self._window_settings.setValue("geometry", self.saveGeometry())
        self._closed = True
        super().done(result)
