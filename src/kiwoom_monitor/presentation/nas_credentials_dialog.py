"""NAS 공급자 인증 설정. 네트워크 작업은 기존 단건 worker에서 실행한다."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QDialog, QFormLayout, QComboBox, QLineEdit, QPushButton, QLabel, QCheckBox, QDialogButtonBox

from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient
from .settings_request_worker import SettingsRequestWorker


_STATE_LABELS = {"VALIDATING": "API 키와 계좌 확인 중", "READY": "계좌 확인 완료 · 적용 대기",
    "DRAINING": "기존 조회·주문 종료 대기", "COMMITTING": "NAS에 저장하고 연결하는 중",
    "BUSY": "실행 중인 작업 종료 대기", "ACTIVE": "적용 완료", "FAILED": "적용 실패",
    "CONFLICT": "설정 버전 충돌", "EXPIRED": "확인 요청 만료", "CANCELLED": "확인 요청 취소",
    "RECOVERY_REQUIRED": "저장은 완료됐을 수 있으나 연결 복구가 필요합니다."}

PROVIDER_LABELS = {"naver": "네이버 뉴스", "dart": "DART 공시", "openai": "OpenAI",
                   "gemini": "Gemini", "claude": "Claude"}
_VALIDATION_LABELS = {"UNVERIFIED": "실제 분석 전 · 키 미검증", "VERIFIED": "인증 확인됨",
    "INVALID_CREDENTIAL": "인증키 확인 필요", "ACCESS_DENIED": "접근 권한 확인 필요",
    "RETRYABLE": "요청 한도 또는 일시적 통신 오류", "REQUEST_FAILED": "분석 요청 확인 필요",
    "DISABLED": "키 비활성화"}


class NasCredentialsDialog(QDialog):
    def __init__(self, client: CentralCredentialsClient, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NAS 모의계좌 관리")
        self.setMinimumWidth(520)
        self._client = client
        self._global = client.provider in client.GLOBAL_FIELDS
        self._real = client.provider == "kiwoom_real"
        self._worker = None
        self._closed = False
        self._started = False
        self._supported = False
        self._operation = None
        self._settings = None
        self._preferred_profile = None
        self._poll_failures = 0
        self._timer = QTimer(self); self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._check_status)
        layout = QFormLayout(self)
        destination = QLabel(client.source.server_url); destination.setWordWrap(True)
        layout.addRow("저장 대상 NAS", destination)
        self._status = QLabel("계좌 설정 지원 여부를 확인합니다."); self._status.setWordWrap(True)
        layout.addRow(self._status)
        self._profiles = QComboBox(); self._profiles.currentIndexChanged.connect(self._profile_changed)
        layout.addRow("모의계좌", self._profiles)
        self._new_label = QLineEdit(); self._new_label.setPlaceholderText("새 계좌 이름")
        layout.addRow("새 계좌", self._new_label)
        self._create_button = QPushButton("새 계좌 추가"); self._create_button.clicked.connect(self._create)
        layout.addRow(self._create_button)
        self._app_key = QLineEdit(); self._app_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._secret_key = QLineEdit(); self._secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._app_key.setMaxLength(4096); self._secret_key.setMaxLength(4096)
        self._app_key.setPlaceholderText("새 키만 입력")
        self._secret_key.setPlaceholderText("새 키만 입력")
        layout.addRow("NAS 모의 App Key", self._app_key)
        layout.addRow("NAS 모의 Secret Key", self._secret_key)
        self._prepare_button = QPushButton("API 키와 계좌 확인"); self._prepare_button.clicked.connect(self._prepare)
        layout.addRow(self._prepare_button)
        self._preview = QLabel("계좌 확인 후 NAS 적용을 선택하세요."); self._preview.setWordWrap(True)
        layout.addRow(self._preview)
        self._apply_button = QPushButton("확인한 계좌에 적용"); self._apply_button.clicked.connect(self._apply)
        self._cancel_button = QPushButton("확인 요청 취소"); self._cancel_button.clicked.connect(self._cancel)
        self._disable_button = QPushButton("이 계좌의 NAS 키 비활성화"); self._disable_button.clicked.connect(self._disable)
        layout.addRow(self._apply_button); layout.addRow(self._cancel_button); layout.addRow(self._disable_button)
        self._monitor = QCheckBox("계좌 조회 사용"); self._monitor.toggled.connect(self._monitor_changed)
        self._orders = QCheckBox("수동 모의주문 허용")
        layout.addRow(self._monitor); layout.addRow(self._orders)
        self._settings_button = QPushButton("계좌 설정 적용"); self._settings_button.clicked.connect(self._save_settings)
        layout.addRow(self._settings_button)
        self._connection_status = QLabel(); self._connection_status.setWordWrap(True)
        layout.addRow(self._connection_status)
        self._connection_status.setVisible(self._real)
        self._reload_button = QPushButton("상태 다시 확인"); self._reload_button.clicked.connect(self._reload)
        layout.addRow(self._reload_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        if self._real:
            self.setWindowTitle("NAS 실전계좌 관리")
            layout.labelForField(self._profiles).setText("실전계좌")
            layout.labelForField(self._app_key).setText("NAS 실전 App Key")
            layout.labelForField(self._secret_key).setText("NAS 실전 Secret Key")
            self._orders.hide()
            self._preview.setText("실전 키와 계좌를 확인한 뒤 적용하세요. 이 화면에서는 주문을 허용하지 않습니다.")
        if self._global:
            label = PROVIDER_LABELS[client.provider]
            self.setWindowTitle(f"NAS {label} API 키 관리")
            layout.labelForField(self._profiles).setText("공급자 연결")
            for widget in (self._new_label, self._create_button, self._monitor, self._orders, self._settings_button):
                widget.hide()
                field_label = layout.labelForField(widget)
                if field_label is not None: field_label.hide()
            layout.labelForField(self._app_key).setText("Client ID" if client.provider == "naver" else "API Key")
            if client.provider == "naver":
                layout.labelForField(self._secret_key).setText("Client Secret")
            else:
                self._secret_key.hide(); layout.labelForField(self._secret_key).hide()
            self._prepare_button.setText("새 키 준비" if client.provider in {"openai", "gemini", "claude"} else "새 키 확인")
            self._apply_button.setText("준비한 키 적용")
            self._disable_button.setText("이 공급자의 NAS 키 비활성화")
            self._preview.setText("새 키를 준비한 뒤 NAS 적용을 선택하세요. PC 직접 연결 키와 별도로 저장합니다.")
        self._refresh()

    def _state_label(self, state):
        if self._real and state == "DRAINING":
            return "기존 조회·실시간 연결 종료 대기"
        if self._global and state in {"VALIDATING", "READY", "DRAINING", "ACTIVE"}:
            return {"VALIDATING": "새 키 준비 중", "READY": "새 키 준비 완료 · 적용 대기",
                    "DRAINING": "기존 수집·분석 완료 대기", "ACTIVE": "NAS 키 적용 완료"}[state]
        return _STATE_LABELS.get(state, "요청 상태를 다시 확인하세요.")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._started:
            self._started = True
            self._load()

    def done(self, result):
        self._closed = True; self._timer.stop()
        self._app_key.clear(); self._secret_key.clear()
        super().done(result)

    def _profile(self):
        value = self._profiles.currentData()
        return value if isinstance(value, dict) else None

    def _refresh(self):
        busy = self._worker is not None or self._closed
        profile = self._profile()
        pending = self._client.pending_profile is not None
        ready = self._operation is not None and self._operation.get("state") == "READY"
        available = bool(self._supported and profile and profile.get("supported") is True
                         and type(profile.get("revision")) is int)
        self._profiles.setEnabled(not busy and not pending)
        self._new_label.setEnabled(not self._global and self._supported and not busy and not pending)
        self._create_button.setEnabled(not self._global and self._supported and not busy and not pending)
        for widget in (self._app_key, self._secret_key, self._prepare_button):
            widget.setEnabled(available and not busy and not ready and (not pending or self._client.operation_id is None))
        self._apply_button.setEnabled(ready and not busy)
        self._cancel_button.setEnabled(not busy and bool(self._operation) and self._operation.get("state") in {"VALIDATING", "READY"})
        self._disable_button.setEnabled(available and not busy and not pending and
            bool(profile.get("configured") if self._global else profile.get("account_ref")))
        settings = not self._global and self._settings is not None and not busy and not pending
        self._monitor.setEnabled(settings); self._orders.setEnabled(settings and self._monitor.isChecked() and not self._real)
        self._settings_button.setEnabled(settings)
        self._reload_button.setEnabled(not busy)

    def _run(self, task, handler, message):
        if self._worker is not None or self._closed: return
        self._timer.stop(); self._status.setText(message)
        worker = SettingsRequestWorker(task); self._worker = worker
        worker.succeeded.connect(handler); worker.failed.connect(self._failed)
        self._refresh(); worker.start()

    def _load(self):
        self._run(self._client.load, self._loaded, "NAS 공급자 상태 확인 중…" if self._global else "NAS 계좌 목록 확인 중…")

    @Slot(object)
    def _loaded(self, result):
        self._worker = None
        if self._closed: return
        self._supported = any(p.get("provider") == self._client.provider and p.get("supported") is True for p in result["providers"])
        current = self._profile()
        target = self._preferred_profile or self._client.pending_profile or (current.get("profile_id") if current else None)
        self._preferred_profile = None
        self._profiles.blockSignals(True); self._profiles.clear()
        for profile in result["profiles"]:
            if profile.get("provider") != self._client.provider or profile.get("profile_id") == "nas-main-mock-default": continue
            if self._global and profile.get("profile_id") != f"nas-{self._client.provider}-default": continue
            status = "비활성" if profile.get("disabled") else "연결됨" if profile.get("runtime") == "ACTIVE" else "연결 확인 필요"
            name = PROVIDER_LABELS[self._client.provider] if self._global else profile.get('label') or ('기본 실전계좌' if self._real else '기본 모의계좌')
            validation = _VALIDATION_LABELS.get(profile.get("runtime_validation"), "") if self._global else ""
            self._profiles.addItem(f"{name} · {status}" + (f" · {validation}" if validation else ""), profile)
            if profile.get("profile_id") == target: self._profiles.setCurrentIndex(self._profiles.count() - 1)
        self._profiles.blockSignals(False)
        self._status.setText("계좌를 선택하고 새 키를 입력하세요." if self._supported else "이 NAS는 해당 계좌의 키 변경을 지원하지 않습니다.")
        if self._global:
            self._status.setText("새 키를 입력하세요." if self._supported else "이 NAS는 해당 공급자의 키 변경을 지원하지 않습니다.")
        if self._operation and self._operation.get("state") in _STATE_LABELS:
            self._status.setText(self._state_label(self._operation["state"]))
        self._refresh()
        if self._client.pending_profile and self._client.operation_id:
            self._check_status()
        else:
            self._load_settings()

    @Slot(int)
    def _profile_changed(self, index):
        self._operation = None; self._settings = None
        self._app_key.clear(); self._secret_key.clear()
        self._preview.setText("새 키 준비 후 NAS 적용을 선택하세요." if self._global else "계좌 확인 후 NAS 적용을 선택하세요.")
        self._refresh(); self._load_settings()

    def _load_settings(self):
        self._settings = None
        self._connection_status.clear()
        profile = self._profile()
        if not self._global and self._supported and profile and profile.get("account_ref") and profile.get("supported") is True:
            ref = profile["account_ref"]
            self._run(lambda: self._client.account_settings(ref), self._settings_loaded, "계좌 설정 확인 중…")
        else: self._refresh()

    @Slot(object)
    def _settings_loaded(self, result):
        self._worker = None
        if self._closed: return
        document = result["settings"]
        profile = self._profile()
        if not profile or document.get("active_profile_id") != profile["profile_id"]:
            self._settings = None
        else:
            self._settings = document
        self._monitor.setChecked(document.get("monitor_enabled") is True)
        self._orders.setChecked(document.get("mock_order_enabled") is True)
        applied = result.get("applied_revision") == document.get("revision")
        terminal = self._operation.get("state") if self._operation else None
        self._status.setText(_STATE_LABELS[terminal] if terminal in _STATE_LABELS else
            "계좌 설정 적용됨" if applied else "계좌 연결 확인 또는 설정 재적용이 필요합니다.")
        if self._real and self._settings is not None:
            monitor = result.get("monitor_status")
            monitor = monitor if isinstance(monitor, dict) else {}
            realtime = monitor.get("realtime")
            realtime = realtime if isinstance(realtime, dict) else {}
            state = realtime.get("state")
            connection = {"ready": "실시간 등록 승인됨", "waiting": "실시간 등록 승인 대기",
                "off": "계좌 조회 꺼짐", "paused": "실시간 연결 변경 중"}.get(state, "실시간 상태 확인 필요")
            read_state = {"collecting": "계좌 조회 수집 중", "waiting": "계좌 조회 대기",
                "off": "계좌 조회 꺼짐", "paused": "계좌 조회 변경 중"}.get(monitor.get("state"), "계좌 조회 상태 확인 필요")
            self._connection_status.setText(f"{read_state} · {connection}")
            if monitor.get("error_code") or realtime.get("error_code"):
                self._connection_status.setText(self._connection_status.text() + " · 수신·저장 오류 확인 필요")
        self._refresh()

    def _create(self):
        label = self._new_label.text().strip()
        create = self._client.create_account_profile if self._real else self._client.create_mock_profile
        self._run(lambda: create(label), self._created, "새 계좌 추가 중…")

    @Slot(object)
    def _created(self, result):
        self._worker = None
        if self._closed: return
        self._preferred_profile = result["profile_id"]
        self._new_label.clear(); self._profiles.setCurrentIndex(-1)
        self._load()

    def _prepare(self):
        profile = self._profile()
        if not profile: return
        key, secret = self._app_key.text(), self._secret_key.text()
        self._app_key.clear(); self._secret_key.clear()
        profile_id, revision = profile["profile_id"], profile["revision"]
        if self._global:
            fields = self._client.GLOBAL_FIELDS[self._client.provider]
            replacement = dict(zip(fields, (key, secret)))
            self._run(lambda: self._client.prepare_global(revision, replacement), self._operation_result, "새 키 준비 중…")
        else:
            prepare = self._client.prepare_account if self._real else self._client.prepare_mock
            self._run(lambda: prepare(profile_id, revision, key, secret), self._operation_result, "API 키와 계좌 확인 중…")

    def _disable(self):
        profile = self._profile()
        if not profile: return
        self._app_key.clear(); self._secret_key.clear()
        profile_id, revision = profile["profile_id"], profile["revision"]
        prepare = self._client.prepare_account if self._real else self._client.prepare_mock
        task = (lambda: self._client.prepare_global(revision, disabled=True)) if self._global else (
            lambda: prepare(profile_id, revision, disabled=True))
        self._run(task, self._operation_result, "비활성화 대상 확인 중…")

    @Slot(object)
    def _operation_result(self, result):
        self._worker = None
        if self._closed: return
        self._poll_failures = 0
        self._operation = result
        state = result.get("state")
        self._client.operation_state = state
        self._status.setText(self._state_label(state))
        ref = result.get("target_account_ref")
        if ref:
            if result.get("disabled"):
                text = "이 계좌의 NAS 키를 비활성화합니다. 매매 이력은 유지됩니다."
            elif not (self._profile() or {}).get("account_ref"):
                text = ("새 실전계좌입니다. 기존 계좌와 매매 이력은 유지됩니다. 키 적용으로 실전 주문이 활성화되지는 않습니다."
                    if self._real else "새 계좌입니다. 기존 계좌와 매매 이력은 유지되며 새 계좌의 주문은 OFF로 시작합니다.")
            else:
                text = "기존 계좌의 키를 교체합니다. 계좌와 매매 이력은 유지됩니다."
            self._preview.setText(f"{text}\n확인한 계좌: {ref}")
        if result.get("error_code") == "ACCOUNT_CHANGED":
            self._preview.setText("다른 계좌의 키입니다. 새 계좌 추가로 등록하세요. 기존 계좌는 유지됩니다.")
        self._apply_button.setText("비활성화 적용" if result.get("disabled") else "확인한 계좌에 적용")
        if self._global:
            self._apply_button.setText("비활성화 적용" if result.get("disabled") else "준비한 키 적용")
            if state in {"FAILED", "CONFLICT", "EXPIRED", "CANCELLED", "RECOVERY_REQUIRED"}:
                text = "요청 상태를 다시 확인하세요. 저장 기사·분석·사용량은 유지됩니다."
            elif result.get("disabled"):
                text = "이 공급자의 키를 비활성화합니다. 저장 기사·분석·사용량은 유지됩니다."
            elif self._client.provider in {"openai", "gemini", "claude"}:
                text = "키 준비·적용은 유료 분석을 실행하지 않습니다. 실제 분석이 성공하면 인증 확인됨으로 표시합니다."
            else:
                text = "새 키 준비 완료 후 NAS 적용을 선택하세요. 저장 기사·분석·사용량은 유지됩니다."
            self._preview.setText(text)
        self._refresh()
        if state in {"VALIDATING", "DRAINING", "COMMITTING", "BUSY"}:
            self._timer.start(750)
        elif state in {"ACTIVE", "RECOVERY_REQUIRED", "FAILED", "EXPIRED", "CANCELLED", "CONFLICT"}:
            self._load()

    def _apply(self):
        if not self._operation or self._operation.get("state") != "READY": return
        revision, ref = self._operation["expected_revision"], self._operation["target_account_ref"]
        self._operation = {**self._operation, "state": "DRAINING"}
        self._run(lambda: self._client.apply(revision, ref), self._operation_result, "NAS에 적용 요청 중…")

    def _cancel(self):
        self._run(self._client.cancel, self._operation_result, "확인 요청 취소 중…")

    def _check_status(self):
        self._run(self._client.status, self._operation_result, "NAS 요청 상태 확인 중…")

    def _reload(self):
        if self._client.pending_profile and self._client.operation_id: self._check_status()
        else: self._load()

    @Slot(bool)
    def _monitor_changed(self, checked):
        if not checked: self._orders.setChecked(False)
        self._refresh()

    def _save_settings(self):
        if not self._settings or not self._profile(): return
        ref, profile = self._settings["scope"]["account_ref"], self._profile()["profile_id"]
        revision, monitor, orders = self._settings["revision"], self._monitor.isChecked(), self._orders.isChecked()
        self._settings = None  # A lost PUT response must be re-read before another CAS.
        self._run(lambda: self._client.update_account(ref, profile, revision, monitor_enabled=monitor,
            mock_order_enabled=orders), self._settings_loaded, "NAS 계좌 설정 적용 중…")

    @Slot(str)
    def _failed(self, message):
        self._worker = None
        if self._closed: return
        self._status.setText(message)
        self._refresh()
        if self._client.pending_profile and self._client.operation_id:
            self._poll_failures += 1
            if self._poll_failures <= 5: self._timer.start(750)
