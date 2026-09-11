"""키움 API 및 NAS 연결 설정 대화상자."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QWidget,
)

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.central_operational_settings import (
    CentralOperationalSettingsClient,
    apply_to_local_news,
)
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomApiError, KiwoomRestClient, KiwoomSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import ApiProfiles, LocalApiConfig
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig
from kiwoom_monitor.infrastructure.news_ai import DEFAULT_MODELS, MODEL_OPTIONS
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700; color: #1F4E79; padding-top: 3px;")
    return label


class NasResourceWorker(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, server_url: str, access_token: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._server_url = server_url.rstrip("/")
        self._access_token = access_token

    def run(self) -> None:
        if not self._server_url or not self._access_token:
            self.failed.emit("NAS 주소와 접속 토큰을 입력하세요.")
            return
        request = Request(
            f"{self._server_url}/api/v1/diagnostics/resources",
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        try:
            with urlopen(request, timeout=10, context=system_ssl_context()) as response:
                document = json.loads(response.read().decode("utf-8"))
            if not isinstance(document, dict):
                raise ValueError("응답 형식이 올바르지 않습니다.")
        except (ValueError, HTTPError, URLError, TimeoutError, OSError) as error:
            self.failed.emit(str(error))
            return
        self.loaded.emit(document)


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
        self._nas_resource_status = QLabel("확인 전")
        self._nas_resource_status.setWordWrap(True)
        self._nas_resource_worker: NasResourceWorker | None = None

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
        route_text = {
            "central": "NAS",
            "central_waiting": "NAS (키움 실시간 대기 중)",
            "local_fallback": "로컬 키움 API (자동 전환 중)",
            "central_retry": "로컬 키움 API (NAS 재연결 확인 중)",
        }.get(active_route, "설정 적용 후 확인")
        self._active_route_status = QLabel(route_text)
        self._active_route_status.setStyleSheet(
            "color: #B36B00; font-weight: bold;"
            if active_route in {"local_fallback", "central_retry"}
            else "color: #008000; font-weight: bold;" if active_route == "central"
            else "color: #B36B00; font-weight: bold;" if active_route == "central_waiting" else ""
        )
        if self._section == "kiwoom":
            layout.addRow("모의 App Key", self._mock_app_key)
            layout.addRow("모의 Secret Key", self._mock_secret_key)
            layout.addRow("실전 App Key", self._real_app_key)
            layout.addRow("실전 Secret Key", self._real_secret_key)
            layout.addRow("이번 실행 환경", self._environment)
        else:
            layout.addRow("현재 실제 연결", self._active_route_status)
            layout.addRow("NAS 주소", self._server_url)
            layout.addRow("NAS 접속 토큰", self._server_token)
            layout.addRow("장애 대응", self._local_fallback)
            layout.addRow("데이터 검증", self._parallel_validation)
            layout.addRow(_section_title("NAS 운영 설정"))
            layout.addRow("적용 상태", self._nas_operations_status)
            layout.addRow("AI 공급자", self._nas_ai_provider)
            layout.addRow("AI 모델", self._nas_ai_model)
            layout.addRow("AI 하루 최대 요청", self._nas_ai_daily_limit)
            layout.addRow("뉴스 수집 주기", self._nas_news_refresh)
            layout.addRow(self._nas_dart_enabled)
            resource_button = QPushButton("사용량 새로고침")
            resource_button.clicked.connect(self._load_nas_resource_usage)
            resource_row = QHBoxLayout()
            resource_row.addWidget(resource_button)
            resource_row.addWidget(self._nas_resource_status, 1)
            layout.addRow("서버 사용량", resource_row)
        self._connection_status = QLabel("연결 확인 전")
        test_button = QPushButton("연결 테스트")
        test_button.clicked.connect(self._test_connection)
        test_row = QHBoxLayout()
        test_row.addWidget(test_button)
        test_row.addWidget(self._connection_status)
        layout.addRow("키움 API 연결" if self._section == "kiwoom" else "NAS 연결", test_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self._data_source_mode.currentIndexChanged.connect(self._refresh_source_controls)
        self._refresh_source_controls()
        if self._section == "nas":
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

    def _validate_and_accept(self) -> None:
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
            try:
                self._save_nas_operational_settings(source)
            except (RuntimeError, ValueError) as error:
                QMessageBox.warning(self, "NAS 운영 설정", str(error))
                return
        DataSourceConfig(self._data_source_path).save(source)
        self.accept()

    def _nas_request(self, method: str, source: DataSourceSettings,
                     body: dict[str, object] | None = None) -> dict[str, object]:
        client = CentralOperationalSettingsClient(source)
        return client.load() if method == "GET" else client.save(body or {})

    def _load_nas_operational_settings(self) -> None:
        try:
            values = self._nas_request("GET", self.data_source_values)
        except (RuntimeError, ValueError) as error:
            self._nas_operations_status.setText(f"불러오기 실패 · {error}")
            self._nas_operations_status.setStyleSheet("color: #C00000; font-weight: bold;")
            self._set_nas_operations_enabled(False)
            return
        self._nas_operations_available = True
        self._nas_operations_status.setText("NAS에 저장된 설정을 불러왔습니다.")
        self._nas_operations_status.setStyleSheet("color: #008000; font-weight: bold;")
        self._set_nas_operations_enabled(True)
        provider_index = self._nas_ai_provider.findData(str(values.get("ai_provider", "none")))
        self._nas_ai_provider.setCurrentIndex(max(0, provider_index))
        self._populate_nas_ai_models(str(values.get("ai_model", "")))
        self._nas_ai_daily_limit.setValue(int(values.get("ai_daily_limit", 0)))
        self._nas_news_refresh.setValue(int(values.get("news_refresh_seconds", 300)))
        self._nas_dart_enabled.setChecked(bool(values.get("dart_enabled", False)))
        try:
            apply_to_local_news(LocalNaverNewsConfig(self._path.with_name("naver_news.dat")), values)
        except (OSError, ValueError):
            # NAS 화면 표시는 성공했으므로 로컬 미러 실패만 상태에 덧붙인다.
            self._nas_operations_status.setText("NAS 설정을 불러왔지만 로컬 뉴스 설정 반영에 실패했습니다.")

    def _set_nas_operations_enabled(self, enabled: bool) -> None:
        for widget in (
            self._nas_ai_provider, self._nas_ai_model, self._nas_ai_daily_limit,
            self._nas_news_refresh, self._nas_dart_enabled,
        ):
            widget.setEnabled(enabled)

    def _save_nas_operational_settings(self, source: DataSourceSettings) -> None:
        values = self._nas_request("PUT", source, {
            "ai_provider": str(self._nas_ai_provider.currentData()),
            "ai_model": str(self._nas_ai_model.currentData() or ""),
            "ai_daily_limit": self._nas_ai_daily_limit.value(),
            "news_refresh_seconds": self._nas_news_refresh.value(),
            "dart_enabled": self._nas_dart_enabled.isChecked(),
        })
        apply_to_local_news(LocalNaverNewsConfig(self._path.with_name("naver_news.dat")), values)

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
        if self._nas_resource_worker is not None and self._nas_resource_worker.isRunning():
            return
        source = self.data_source_values
        self._nas_resource_status.setText("확인 중…")
        # 설정 창이 닫혀도 실행 중인 네트워크 스레드를 강제 파괴하지 않는다.
        worker = NasResourceWorker(source.server_url, source.access_token, QApplication.instance())
        self._nas_resource_worker = worker
        worker.loaded.connect(self._show_nas_resource_usage)
        worker.failed.connect(lambda message: self._nas_resource_status.setText(f"확인 실패 · {message}"))
        worker.finished.connect(lambda: setattr(self, "_nas_resource_worker", None))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _show_nas_resource_usage(self, values: object) -> None:
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
        self._nas_resource_status.setText(
            f"앱 {process} · 컨테이너 {container}{limit_text}\n"
            f"DB {database} · 저장소 {disk_used} / {disk_total}"
        )

    def _test_connection(self) -> None:
        profiles = self.values
        source = self.data_source_values
        if self._section == "nas":
            try:
                source.validate()
                request = Request(
                    f"{source.server_url}/api/v1/capabilities",
                    headers={"Authorization": f"Bearer {source.access_token}"},
                )
                with urlopen(request, timeout=10, context=system_ssl_context()) as response:
                    document = json.loads(response.read().decode("utf-8"))
                if not isinstance(document, dict) or "api_version" not in document:
                    raise RuntimeError("NAS 응답 형식이 올바르지 않습니다.")
            except (ValueError, RuntimeError, HTTPError, URLError, TimeoutError, OSError) as error:
                self._connection_status.setText("NAS 연결 실패")
                self._connection_status.setStyleSheet("color: #C00000; font-weight: bold;")
                QMessageBox.warning(self, "NAS 연결 테스트", f"NAS 연결에 실패했습니다.\n{error}")
            else:
                self._connection_status.setText("NAS 연결 성공")
                self._connection_status.setStyleSheet("color: #008000; font-weight: bold;")
            return
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
