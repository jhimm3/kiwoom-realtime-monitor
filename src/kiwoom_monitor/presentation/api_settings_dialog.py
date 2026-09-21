"""키움 API 및 NAS 연결 설정 대화상자."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from urllib.request import Request, urlopen

from PySide6.QtCore import Slot, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.central_operational_settings import (
    CentralOperationalSettingsClient,
    apply_to_local_news,
)
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient, KiwoomSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import ApiProfiles, LocalApiConfig
from kiwoom_monitor.infrastructure.local_storage_diagnostics import inspect_local_storage
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig
from kiwoom_monitor.infrastructure.news_ai import DEFAULT_MODELS, MODEL_OPTIONS
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context
from kiwoom_monitor.presentation.settings_request_worker import SettingsRequestWorker
from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient
from kiwoom_monitor.presentation.nas_credentials_dialog import NasCredentialsDialog, PROVIDER_LABELS


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700; color: #1F4E79; padding-top: 3px;")
    return label


def _load_resource_document(source: DataSourceSettings) -> dict[str, object]:
    if not source.server_url or not source.access_token:
        raise ValueError("NAS 주소와 접속 토큰을 입력하세요.")
    request = Request(
        f"{source.server_url.rstrip('/')}/api/v1/diagnostics/resources",
        headers={"Authorization": f"Bearer {source.access_token}"},
    )
    with urlopen(request, timeout=10, context=system_ssl_context()) as response:
        document = json.loads(response.read().decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("응답 형식이 올바르지 않습니다.")
    return document


class ApiSettingsDialog(QDialog):
    def __init__(self, path: Path, parent: QWidget | None = None, *, active_route: str = "",
                 section: str = "kiwoom", initial_mode: str | None = None) -> None:
        super().__init__(parent)
        self._section = "nas" if section == "nas" else "kiwoom"
        self.setWindowTitle("NAS 연결 설정" if self._section == "nas" else "키움 API 설정")
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
        self._data_source_path = path.with_name("data_source.json")
        try:
            self._data_source = DataSourceConfig(self._data_source_path).load()
        except (ValueError, OSError, json.JSONDecodeError):
            self._data_source = DataSourceSettings()
        self._data_source_mode = QComboBox()
        self._data_source_mode.addItem("이 PC에서 키움 API 직접 연결", "local")
        self._data_source_mode.addItem("이 PC의 Docker 서버 사용 (앱 재시작 후 적용)", "local_server")
        self._data_source_mode.addItem("NAS로 연결 (앱 재시작 후 전체 적용)", "personal_server")
        mode_index = self._data_source_mode.findData(self._data_source.mode)
        self._data_source_mode.setCurrentIndex(max(0, mode_index))
        if initial_mode in {"local", "local_server", "personal_server"}:
            self._data_source_mode.setCurrentIndex(self._data_source_mode.findData(initial_mode))
        self._server_url = QLineEdit(self._data_source.server_url)
        self._server_url.setPlaceholderText("예: https://monitor.example.com")
        self._server_token = QLineEdit(self._data_source.access_token)
        self._server_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._local_fallback = QCheckBox("NAS 연결 장애 시 이 PC의 키움 API로 자동 전환")
        self._local_fallback.setChecked(self._data_source.local_fallback_enabled)
        self._local_fallback.setToolTip(
            "NAS 연결이 끊겼을 때만 로컬 조회·실시간 연결을 사용합니다.\n"
            "NAS와 로컬 실시간 연결을 동시에 열지는 않습니다."
        )
        self._parallel_validation = QCheckBox("NAS·로컬 조회·실시간 값을 백그라운드에서 병행 비교")
        self._parallel_validation.setChecked(self._data_source.parallel_validation_enabled)
        self._parallel_validation.setToolTip(
            "검증하는 동안 로컬 조회 TR과 보조 실시간 연결을 추가로 사용합니다. 평소에는 꺼두는 설정입니다."
        )
        self._nas_ai_provider = QComboBox()
        self._nas_operations_available = False
        self._nas_operations_status = QLabel("NAS에서 확인 중…")
        self._nas_operations_status.setWordWrap(True)
        for label, value in (("사용 안 함", "none"), ("Google Gemini", "gemini"), ("OpenAI", "openai"), ("Anthropic Claude", "claude")):
            self._nas_ai_provider.addItem(label, value)
        self._nas_ai_model = QComboBox()
        self._nas_ai_provider.currentIndexChanged.connect(self._populate_nas_ai_models)
        self._populate_nas_ai_models()
        self._nas_ai_daily_limit = QSpinBox(); self._nas_ai_daily_limit.setRange(0, 1_000_000)
        self._nas_ai_daily_limit.setSpecialValueText("무제한")
        self._nas_news_refresh = QSpinBox(); self._nas_news_refresh.setRange(60, 86_400); self._nas_news_refresh.setSuffix("초")
        self._nas_dart_enabled = QCheckBox("DART 공시 수집 사용")
        self._nas_query_available = False
        self._nas_query_enabled = QCheckBox("공통 검색어 뉴스 수집 사용")
        self._nas_query_text = QPlainTextEdit()
        self._nas_query_text.setPlaceholderText("검색어를 한 줄에 하나씩 입력하세요. 예: 수주 계약")
        self._nas_query_text.setMaximumHeight(96)
        self._nas_query_refresh = QSpinBox()
        self._nas_query_refresh.setRange(60, 86_400)
        self._nas_query_refresh.setSuffix("초")
        self._nas_external_available = False
        self._nas_external_enabled = QCheckBox("해외 지연 시세 수집 사용")
        self._nas_external_poll = QSpinBox(); self._nas_external_poll.setRange(60, 86_400); self._nas_external_poll.setSuffix("초")
        self._nas_external_roll = QCheckBox("자동 월물 전환 사용")
        self._nas_external_confirmations = QSpinBox(); self._nas_external_confirmations.setRange(1, 100)
        self._nas_condition_available = False
        self._nas_condition_enabled = QCheckBox("조건검색 종목 추적 사용")
        self._nas_condition_name = QLineEdit(); self._nas_condition_name.setMaxLength(120)
        self._nas_condition_name.setPlaceholderText("입력하면 이 이름을 우선 사용")
        self._nas_condition_substring = QLineEdit(); self._nas_condition_substring.setMaxLength(120)
        self._nas_condition_status = QLabel("지원 여부 확인 전")
        self._nas_condition_status.setWordWrap(True); self._nas_condition_status.setTextFormat(Qt.TextFormat.PlainText)
        self._nas_resource_status = QLabel("확인 전")
        self._nas_resource_status.setWordWrap(True)
        self._nas_resource_worker: SettingsRequestWorker | None = None
        self._local_resource_status = QLabel("확인 전")
        self._local_resource_status.setWordWrap(True)
        self._local_resource_worker: SettingsRequestWorker | None = None
        self._operations_worker: SettingsRequestWorker | None = None
        self._connection_worker: SettingsRequestWorker | None = None
        self._nas_client: CentralOperationalSettingsClient | None = None
        self._loaded_source: DataSourceSettings | None = None
        self._operations_baseline: dict[str, object] = {}
        self._closed = False
        self._initial_load_started = False

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

        if self._section == "nas":
            content = QWidget()
            layout = QFormLayout(content)
            self._nas_scroll = QScrollArea()
            self._nas_scroll.setWidgetResizable(True)
            self._nas_scroll.setWidget(content)
            outer_layout = QVBoxLayout(self)
            outer_layout.addWidget(self._nas_scroll)
            self.setMinimumSize(420, 340)
            self.resize(560, 720)
        else:
            layout = QFormLayout(self)
        route_text = {
            "central": "NAS",
            "central_waiting": "NAS",
            "local_fallback": "로컬 키움 API (자동 전환 중)",
            "central_retry": "로컬 키움 API (NAS 재연결 확인 중)",
        }.get(active_route, "설정 적용 후 확인")
        self._active_route_status = QLabel(route_text)
        self._active_route_status.setStyleSheet(
            "color: #B36B00; font-weight: bold;"
            if active_route in {"local_fallback", "central_retry"}
            else "color: #008000; font-weight: bold;" if active_route in {"central", "central_waiting"}
            else ""
        )
        if self._section == "kiwoom":
            layout.addRow("모의 App Key", self._mock_app_key)
            layout.addRow("모의 Secret Key", self._mock_secret_key)
            layout.addRow("실전 App Key", self._real_app_key)
            layout.addRow("실전 Secret Key", self._real_secret_key)
            layout.addRow("이번 실행 환경", self._environment)
            local_resource_button = QPushButton("사용량 새로고침")
            local_resource_button.clicked.connect(self._load_local_resource_usage)
            local_resource_row = QHBoxLayout()
            local_resource_row.addWidget(local_resource_button)
            local_resource_row.addWidget(self._local_resource_status, 1)
            layout.addRow("이 PC 저장량", local_resource_row)
        else:
            layout.addRow("현재 실제 연결", self._active_route_status)
            layout.addRow("NAS 주소", self._server_url)
            layout.addRow("NAS 접속 토큰", self._server_token)
            self._nas_credentials_client = None
            self._nas_credentials_button = QPushButton("NAS 모의계좌·API 키 관리")
            self._nas_credentials_button.clicked.connect(self._open_nas_credentials)
            layout.addRow(self._nas_credentials_button)
            self._nas_provider_clients = {}
            self._nas_real_credentials_button = QPushButton("NAS 실전계좌·API 키 관리")
            self._nas_real_credentials_button.clicked.connect(
                lambda: self._open_nas_provider_credentials("kiwoom_real"))
            layout.addRow(self._nas_real_credentials_button)
            self._nas_provider_button = QPushButton("NAS 뉴스·AI API 키 관리")
            provider_menu = QMenu(self._nas_provider_button)
            for provider, label in PROVIDER_LABELS.items():
                action = provider_menu.addAction(label)
                action.triggered.connect(lambda checked=False, selected=provider: self._open_nas_provider_credentials(selected))
            self._nas_provider_button.setMenu(provider_menu)
            layout.addRow(self._nas_provider_button)
            layout.addRow("장애 대응", self._local_fallback)
            layout.addRow("데이터 검증", self._parallel_validation)
            layout.addRow(_section_title("NAS 운영 설정"))
            layout.addRow("적용 상태", self._nas_operations_status)
            reload_button = QPushButton("운영 설정 다시 불러오기")
            reload_button.clicked.connect(self._load_nas_operational_settings)
            layout.addRow(reload_button)
            layout.addRow("AI 공급자", self._nas_ai_provider)
            layout.addRow("AI 모델", self._nas_ai_model)
            layout.addRow("AI 하루 최대 요청", self._nas_ai_daily_limit)
            layout.addRow("등록 종목 뉴스 수집 주기", self._nas_news_refresh)
            layout.addRow(self._nas_dart_enabled)
            layout.addRow(_section_title("공통 뉴스 검색"))
            layout.addRow(self._nas_query_enabled)
            layout.addRow("검색어 (최대 50개)", self._nas_query_text)
            layout.addRow("검색어별 수집 주기", self._nas_query_refresh)
            query_note = QLabel("검색어는 한 줄에 하나씩 입력합니다. 공통 검색은 종목별 수집과 별도로 실행되며 하루 요청 한도 안에서 수집합니다.")
            query_note.setWordWrap(True)
            layout.addRow(query_note)
            layout.addRow(_section_title("조건검색"))
            layout.addRow(self._nas_condition_enabled)
            layout.addRow("조건식 정확한 이름", self._nas_condition_name)
            layout.addRow("이름에 포함된 문자열", self._nas_condition_substring)
            layout.addRow("조건식 적용 상태", self._nas_condition_status)
            layout.addRow(_section_title("해외 지연 시세"))
            layout.addRow(self._nas_external_enabled)
            layout.addRow("수집 주기", self._nas_external_poll)
            layout.addRow(self._nas_external_roll)
            layout.addRow("월물 전환 확인 횟수", self._nas_external_confirmations)
            resource_button = QPushButton("사용량 새로고침")
            resource_button.clicked.connect(self._load_nas_resource_usage)
            resource_row = QHBoxLayout()
            resource_row.addWidget(resource_button)
            resource_row.addWidget(self._nas_resource_status, 1)
            layout.addRow("서버 사용량", resource_row)
        self._connection_status = QLabel("연결 확인 전")
        test_button = QPushButton("연결 테스트")
        self._test_button = test_button
        test_button.clicked.connect(self._test_connection)
        test_row = QHBoxLayout()
        test_row.addWidget(test_button)
        test_row.addWidget(self._connection_status)
        layout.addRow("키움 API 연결" if self._section == "kiwoom" else "NAS 연결", test_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self._buttons = buttons
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        if self._section == "nas":
            outer_layout.addWidget(buttons)
        else:
            layout.addRow(buttons)
        self._data_source_mode.currentIndexChanged.connect(self._refresh_source_controls)
        self._refresh_source_controls()
        self._set_nas_operations_enabled(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._closed = False
        if self._section == "nas" and not self._initial_load_started:
            self._initial_load_started = True
            self._load_nas_operational_settings()

    @property
    def values(self) -> ApiProfiles:
        return ApiProfiles(
            mock_app_key=self._mock_app_key.text().strip(), mock_secret_key=self._mock_secret_key.text().strip(),
            real_app_key=self._real_app_key.text().strip(), real_secret_key=self._real_secret_key.text().strip(),
            active_environment=str(self._environment.currentData()),
        )

    @property
    def data_source_values(self) -> DataSourceSettings:
        mode = str(self._data_source_mode.currentData())
        return DataSourceSettings(
            mode=mode,
            server_url=self._server_url.text().strip().rstrip("/"),
            access_token=self._server_token.text().strip(),
            local_fallback_enabled=(
                self._local_fallback.isChecked() if mode == "personal_server" else False
            ),
            parallel_validation_enabled=(
                self._parallel_validation.isChecked() if mode == "personal_server" else False
            ),
        )

    def _refresh_source_controls(self) -> None:
        mode = str(self._data_source_mode.currentData())
        if mode == "local_server":
            if not self._server_url.text().strip():
                self._server_url.setText("http://127.0.0.1:8787")
            if not self._server_token.text().strip():
                self._server_token.setText(secrets.token_urlsafe(32))
        central = mode in {"local_server", "personal_server"}
        personal = mode == "personal_server"
        self._server_url.setEnabled(central)
        self._server_token.setEnabled(central)
        self._local_fallback.setEnabled(personal)
        self._parallel_validation.setEnabled(personal)
        if hasattr(self, "_nas_credentials_button"):
            self._nas_credentials_button.setEnabled(personal)
            self._nas_real_credentials_button.setEnabled(personal)
            self._nas_provider_button.setEnabled(personal)

    def _open_nas_credentials(self):
        source = self.data_source_values
        previous = self._nas_credentials_client.source if self._nas_credentials_client else None
        if previous is None or (previous.mode, previous.server_url, previous.access_token) != (
                source.mode, source.server_url, source.access_token):
            self._nas_credentials_client = CentralCredentialsClient(source)
        dialog = NasCredentialsDialog(self._nas_credentials_client, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _open_nas_provider_credentials(self, provider):
        source = self.data_source_values
        client = self._nas_provider_clients.get(provider)
        previous = client.source if client else None
        if previous is None or (previous.mode, previous.server_url, previous.access_token) != (
                source.mode, source.server_url, source.access_token):
            client = CentralCredentialsClient(source, provider=provider)
            self._nas_provider_clients[provider] = client
        dialog = NasCredentialsDialog(client, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _validate_and_accept(self) -> None:
        if self._operations_worker is not None:
            return
        profiles = self.values
        source = self.data_source_values
        if self._section == "kiwoom":
            active_pair = (profiles.real_app_key, profiles.real_secret_key) if profiles.active_environment == "real" else (profiles.mock_app_key, profiles.mock_secret_key)
            local_key_required = source.mode == "local" or source.local_fallback_enabled or source.parallel_validation_enabled
            if local_key_required and not all(active_pair):
                QMessageBox.warning(self, "입력 확인", "이번 실행 환경의 App Key와 Secret Key를 모두 입력하세요.")
                return
            self.accept()
            return
        active_pair = (profiles.real_app_key, profiles.real_secret_key) if profiles.active_environment == "real" else (profiles.mock_app_key, profiles.mock_secret_key)
        local_key_required = source.mode == "local" or source.local_fallback_enabled or source.parallel_validation_enabled
        if local_key_required and not all(active_pair):
            QMessageBox.warning(self, "입력 확인", "이번 실행 환경의 App Key와 Secret Key를 모두 입력하세요.")
            return
        try:
            source.validate()
        except ValueError as error:
            QMessageBox.warning(self, "입력 확인", str(error))
            return
        if self._nas_operations_available:
            if self._loaded_source is None or source.server_url != self._loaded_source.server_url or source.access_token != self._loaded_source.access_token:
                self._nas_operations_available = False
                self._load_nas_operational_settings()
                self._nas_operations_status.setText("변경한 NAS 설정을 확인한 뒤 다시 저장하세요.")
                return
            self._save_nas_operational_settings(source)
            return
        DataSourceConfig(self._data_source_path).save(source)
        self.accept()

    def _load_nas_operational_settings(self) -> None:
        if self._operations_worker is not None:
            return
        source = self.data_source_values
        self._nas_client = CentralOperationalSettingsClient(source)
        self._loaded_source = source
        self._nas_operations_status.setText("NAS 설정을 불러오는 중…")
        self._set_nas_operations_enabled(False)
        worker = SettingsRequestWorker(self._nas_client.load)
        self._operations_worker = worker
        worker.succeeded.connect(self._operations_loaded)
        worker.failed.connect(self._operations_failed)
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        worker.start()

    @Slot(object)
    def _operations_loaded(self, values: dict[str, object]) -> None:
        self._operations_worker = None
        if self._closed:
            return
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
        self._nas_operations_available = True
        self._nas_query_available = all(name in values for name in (
            "news_query_set_enabled", "news_query_set", "news_query_set_refresh_seconds"))
        self._nas_condition_available = values.get("condition_runtime_supported") is True and all(name in values for name in (
            "hot_cohort_condition_enabled", "hot_cohort_condition_name", "hot_cohort_condition_substring"))
        self._nas_external_available = all(name in values for name in (
            "external_market_enabled", "external_market_poll_seconds", "external_market_auto_roll_enabled",
            "external_market_roll_confirmations"))
        pending = values.get("apply_status") == "RECOVERY_REQUIRED"
        self._nas_operations_status.setText("NAS에 저장됐지만 실행 적용 확인이 필요합니다. 다시 저장해 적용하세요." if pending else
                                           "NAS에 저장된 설정을 불러왔습니다.")
        self._nas_operations_status.setStyleSheet("color: #B36B00; font-weight: bold;" if pending else
                                                 "color: #008000; font-weight: bold;")
        self._set_nas_operations_enabled(True)
        provider_index = self._nas_ai_provider.findData(str(values.get("ai_provider", "none")))
        self._nas_ai_provider.setCurrentIndex(max(0, provider_index))
        self._populate_nas_ai_models(str(values.get("ai_model", "")))
        self._nas_ai_daily_limit.setValue(int(values.get("ai_daily_limit", 0)))
        self._nas_news_refresh.setValue(int(values.get("news_refresh_seconds", 300)))
        self._nas_dart_enabled.setChecked(bool(values.get("dart_enabled", False)))
        self._nas_query_enabled.setChecked(bool(values.get("news_query_set_enabled", False)))
        self._nas_query_text.setPlainText("\n".join(str(query) for query in values.get("news_query_set", [])))
        self._nas_query_refresh.setValue(int(values.get("news_query_set_refresh_seconds", 300)))
        self._nas_condition_enabled.setChecked(bool(values.get("hot_cohort_condition_enabled", True)))
        self._nas_condition_name.setText(str(values.get("hot_cohort_condition_name", "")))
        self._nas_condition_substring.setText(str(values.get("hot_cohort_condition_substring", "15%")))
        condition = values.get("condition_status")
        phase = condition.get("apply_status") if isinstance(condition, dict) else None
        phase_label = {"ACTIVE": "조건식 등록 완료", "OFF": "조건검색 추적 중지 · 기존 종목 자료 유지",
            "RECOVERY_REQUIRED": "조건식 적용 확인 필요 · 다시 저장해 재확인", "WAITING_CLEAR": "이전 조건식 해제 대기",
            "WAITING_LIST": "조건식 목록 확인 대기", "WAITING_REGISTER": "새 조건식 등록 대기",
            "WAITING_CONNECTION": "키움 WebSocket 연결 대기"}.get(phase, "이 NAS의 조건검색 운영 연결을 확인하세요.")
        active = condition.get("active") if isinstance(condition, dict) else None
        self._nas_condition_status.setText(phase_label + (f"\n현재 활성 조건식: {active[1]}" if isinstance(active, list) and len(active) == 2 else ""))
        self._nas_external_enabled.setChecked(bool(values.get("external_market_enabled", False)))
        self._nas_external_poll.setValue(int(values.get("external_market_poll_seconds", 300)))
        self._nas_external_roll.setChecked(bool(values.get("external_market_auto_roll_enabled", True)))
        self._nas_external_confirmations.setValue(int(values.get("external_market_roll_confirmations", 2)))
        self._operations_baseline = self._nas_operation_values()
        try:
            apply_to_local_news(LocalNaverNewsConfig(self._path.with_name("naver_news.dat")), values)
        except (OSError, ValueError):
            # NAS 화면 표시는 성공했으므로 로컬 미러 실패만 상태에 덧붙인다.
            self._nas_operations_status.setText("NAS 설정을 불러왔지만 로컬 뉴스 설정 반영에 실패했습니다.")

    @Slot(str)
    def _operations_failed(self, message: str) -> None:
        self._operations_worker = None
        if self._closed:
            return
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
        self._nas_operations_status.setText(f"NAS 설정 실패 · {message}")
        self._nas_operations_status.setStyleSheet("color: #C00000; font-weight: bold;")
        # Reload is explicit after any failed operation; do not silently save
        # controls based on a stale revision or uncertain network result.
        self._nas_operations_available = False
        self._set_nas_operations_enabled(False)
        self._data_source_mode.setEnabled(True)
        self._refresh_source_controls()

    def _set_nas_operations_enabled(self, enabled: bool) -> None:
        for widget in (
            self._nas_ai_provider, self._nas_ai_model, self._nas_ai_daily_limit,
            self._nas_news_refresh, self._nas_dart_enabled,
        ):
            widget.setEnabled(enabled)
        for widget in (self._nas_external_enabled, self._nas_external_poll,
                       self._nas_external_roll, self._nas_external_confirmations):
            widget.setEnabled(enabled and self._nas_external_available)
        for widget in (self._nas_condition_enabled, self._nas_condition_name, self._nas_condition_substring):
            widget.setEnabled(enabled and self._nas_condition_available)
        for widget in (self._nas_query_enabled, self._nas_query_text, self._nas_query_refresh):
            widget.setEnabled(enabled and self._nas_query_available)

    def _nas_operation_values(self) -> dict[str, object]:
        values = {
            "ai_provider": str(self._nas_ai_provider.currentData()),
            "ai_model": str(self._nas_ai_model.currentData() or ""),
            "ai_daily_limit": self._nas_ai_daily_limit.value(),
            "news_refresh_seconds": self._nas_news_refresh.value(),
            "dart_enabled": self._nas_dart_enabled.isChecked(),
        }
        if self._nas_external_available:
            values.update(external_market_enabled=self._nas_external_enabled.isChecked(),
                external_market_poll_seconds=self._nas_external_poll.value(),
                external_market_auto_roll_enabled=self._nas_external_roll.isChecked(),
                external_market_roll_confirmations=self._nas_external_confirmations.value())
        if self._nas_condition_available:
            values.update(hot_cohort_condition_enabled=self._nas_condition_enabled.isChecked(),
                hot_cohort_condition_name=self._nas_condition_name.text().strip(),
                hot_cohort_condition_substring=self._nas_condition_substring.text().strip())
        if self._nas_query_available:
            values.update(news_query_set_enabled=self._nas_query_enabled.isChecked(),
                news_query_set=list(dict.fromkeys(query.strip() for query in self._nas_query_text.toPlainText().splitlines() if query.strip())),
                news_query_set_refresh_seconds=self._nas_query_refresh.value())
        return values

    def _save_nas_operational_settings(self, source: DataSourceSettings) -> None:
        if self._nas_query_available:
            queries = self._nas_operation_values()["news_query_set"]
            if len(queries) > 50:
                QMessageBox.warning(self, "입력 확인", "공통 뉴스 검색어는 중복을 제외해 최대 50개까지 입력하세요.")
                return
            if self._nas_query_enabled.isChecked() and not queries:
                QMessageBox.warning(self, "입력 확인", "공통 뉴스 수집을 사용하려면 검색어를 한 개 이상 입력하세요.")
                return
        client = self._nas_client
        changes = {name: value for name, value in self._nas_operation_values().items()
                   if value != self._operations_baseline.get(name)}
        source_path = self._data_source_path
        news_path = self._path.with_name("naver_news.dat")

        def save() -> object:
            values = client.save(changes)
            DataSourceConfig(source_path).save(source)
            apply_to_local_news(LocalNaverNewsConfig(news_path), values)
            return values

        self._set_nas_operations_enabled(False)
        for widget in (self._data_source_mode, self._server_url, self._server_token,
                       self._local_fallback, self._parallel_validation):
            widget.setEnabled(False)
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self._nas_operations_status.setText("NAS 설정을 저장하는 중…")
        worker = SettingsRequestWorker(save)
        self._operations_worker = worker
        worker.succeeded.connect(self._operations_saved)
        worker.failed.connect(self._operations_failed)
        worker.start()

    @Slot(object)
    def _operations_saved(self, values: object) -> None:
        self._operations_worker = None
        if not self._closed:
            self.accept()

    def done(self, result: int) -> None:
        self._closed = True
        super().done(result)

    def _populate_nas_ai_models(self, saved_model: object = None) -> None:
        provider = str(self._nas_ai_provider.currentData())
        target = (
            str(saved_model) if isinstance(saved_model, str) and saved_model
            else DEFAULT_MODELS.get(provider, "")
        )
        self._nas_ai_model.clear()
        if provider == "none":
            self._nas_ai_model.addItem("AI 공급자를 먼저 선택하세요", "")
            self._nas_ai_model.setEnabled(False)
            return
        self._nas_ai_model.setEnabled(True)
        for label, model_id in MODEL_OPTIONS.get(provider, ()):
            self._nas_ai_model.addItem(label, model_id)
        index = self._nas_ai_model.findData(target)
        if index < 0 and target:
            self._nas_ai_model.addItem(f"기존 저장 모델 · {target}", target)
            index = self._nas_ai_model.count() - 1
        self._nas_ai_model.setCurrentIndex(max(0, index))

    @staticmethod
    def _format_bytes(value: object) -> str:
        if not isinstance(value, (int, float)) or value < 0:
            return "확인 불가"
        amount = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if amount < 1024 or unit == "TB":
                return f"{amount:.1f}{unit}" if unit != "B" else f"{int(amount)}B"
            amount /= 1024
        return "확인 불가"

    def _load_nas_resource_usage(self) -> None:
        if self._nas_resource_worker is not None:
            return
        source = self.data_source_values
        self._nas_resource_status.setText("확인 중…")
        # 설정 창이 닫혀도 실행 중인 네트워크 스레드를 강제 파괴하지 않는다.
        worker = SettingsRequestWorker(lambda: _load_resource_document(source))
        self._nas_resource_worker = worker
        worker.succeeded.connect(self._show_nas_resource_usage)
        worker.failed.connect(self._resource_failed)
        worker.start()

    def _load_local_resource_usage(self) -> None:
        if self._local_resource_worker is not None:
            return
        self._local_resource_status.setText("확인 중…")
        worker = SettingsRequestWorker(lambda: inspect_local_storage(self._path.parent))
        self._local_resource_worker = worker
        worker.succeeded.connect(self._show_local_resource_usage)
        worker.failed.connect(self._local_resource_failed)
        worker.start()

    @Slot(str)
    def _local_resource_failed(self, message: str) -> None:
        self._local_resource_worker = None
        if not self._closed:
            self._local_resource_status.setText(f"확인 실패 · {message}")

    @Slot(object)
    def _show_local_resource_usage(self, values: object) -> None:
        self._local_resource_worker = None
        if self._closed:
            return
        if not isinstance(values, dict):
            self._local_resource_status.setText("응답 형식 오류")
            return
        lines = [f"전체 {self._format_bytes(values.get('total_bytes'))}"]
        categories = values.get("categories", [])
        if isinstance(categories, list):
            for item in categories:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"{item.get('label', '기타')} {self._format_bytes(item.get('bytes'))}"
                    f" · {int(item.get('files', 0)):,}개 파일"
                )
        retention = values.get("retention", [])
        if isinstance(retention, list):
            lines.extend(str(item) for item in retention)
        if values.get("complete") is False:
            lines.append("일부 파일은 사용 중이거나 접근할 수 없어 제외됨")
        self._local_resource_status.setText("\n".join(lines))

    @Slot(str)
    def _resource_failed(self, message: str) -> None:
        self._nas_resource_worker = None
        if not self._closed:
            self._nas_resource_status.setText(f"확인 실패 · {message}")

    @Slot(object)
    def _show_nas_resource_usage(self, values: object) -> None:
        self._nas_resource_worker = None
        if self._closed:
            return
        if not isinstance(values, dict):
            self._nas_resource_status.setText("응답 형식 오류")
            return
        process = self._format_bytes(values.get("process_memory_bytes"))
        container = self._format_bytes(values.get("container_memory_bytes"))
        limit = self._format_bytes(values.get("container_memory_limit_bytes"))
        database = self._format_bytes(values.get("database_size_bytes"))
        disk_used = self._format_bytes(values.get("data_disk_used_bytes"))
        disk_total = self._format_bytes(values.get("data_disk_total_bytes"))
        limit_text = f" / {limit}" if values.get("container_memory_limit_bytes") is not None else ""
        category_lines = []
        categories = values.get("storage_categories", [])
        if isinstance(categories, list):
            for item in categories:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label", "기타"))
                size = self._format_bytes(item.get("estimated_bytes"))
                rows = item.get("rows")
                row_text = f" · {int(rows):,}건" if isinstance(rows, (int, float)) else ""
                category_lines.append(f"{label} {size}{row_text}")
        retention = values.get("retention_policy", {})
        retention_text = "자동 정리: 무제한 저장"
        if isinstance(retention, dict) and retention.get("automatic_deletion_enabled"):
            retention_text = "자동 정리: 사용 중"
        self._nas_resource_status.setText(
            f"앱 {process} · 컨테이너 {container}{limit_text}\n"
            f"DB {database} · 저장소 {disk_used} / {disk_total}"
            + ("\n" + "\n".join(category_lines) if category_lines else "")
            + f"\n{retention_text}"
        )

    def _test_connection(self) -> None:
        if self._connection_worker is not None:
            return
        profiles = self.values
        source = self.data_source_values
        if self._section == "nas":
            try:
                source.validate()
            except ValueError as error:
                QMessageBox.warning(self, "입력 확인", str(error))
                return

            def test() -> object:
                request = Request(
                    f"{source.server_url}/api/v1/capabilities",
                    headers={"Authorization": f"Bearer {source.access_token}"},
                )
                with urlopen(request, timeout=10, context=system_ssl_context()) as response:
                    document = json.loads(response.read().decode("utf-8"))
                if not isinstance(document, dict) or "api_version" not in document:
                    raise RuntimeError("NAS 응답 형식이 올바르지 않습니다.")
                return None
        else:
            app_key, secret_key = (
                (profiles.real_app_key, profiles.real_secret_key)
                if profiles.active_environment == "real"
                else (profiles.mock_app_key, profiles.mock_secret_key)
            )
            if not app_key or not secret_key:
                QMessageBox.warning(self, "입력 확인", "선택한 환경의 App Key와 Secret Key를 모두 입력하세요.")
                return

            def test() -> object:
                KiwoomRestClient(KiwoomSettings(app_key, secret_key, profiles.active_environment)).get_access_token()
                return None

        self._connection_status.setText("연결 중…")
        self._connection_status.setStyleSheet("color: #B36B00; font-weight: bold;")
        self._test_button.setEnabled(False)
        worker = SettingsRequestWorker(test)
        self._connection_worker = worker
        worker.succeeded.connect(self._connection_succeeded)
        worker.failed.connect(self._connection_failed)
        worker.start()

    @Slot(object)
    def _connection_succeeded(self, _result: object) -> None:
        self._connection_worker = None
        if self._closed:
            return
        self._test_button.setEnabled(True)
        self._connection_status.setText("연결 성공")
        self._connection_status.setStyleSheet("color: #008000; font-weight: bold;")

    @Slot(str)
    def _connection_failed(self, message: str) -> None:
        self._connection_worker = None
        if self._closed:
            return
        self._test_button.setEnabled(True)
        self._connection_status.setText(f"연결 실패 · {message}")
        self._connection_status.setStyleSheet("color: #C00000; font-weight: bold;")
