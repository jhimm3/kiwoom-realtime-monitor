from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from PySide6.QtCore import QSettings, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from kiwoom_monitor.application.mock_automation_specification import (
    MOCK_AUTOMATION_LIMIT_FIELDS,
    build_mock_automation_publication_documents,
    matching_shadow_events,
)
from kiwoom_monitor.domain.execution_activation import ForwardCriteria
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient


def available_mock_profiles(document: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    values = document.get("profiles", ())
    if not isinstance(values, list):
        return ()
    profiles = [
        value for value in values
        if isinstance(value, dict)
        and value.get("provider") == "kiwoom_mock"
        and value.get("supported") is True
        and value.get("configured") is True
        and value.get("disabled") is not True
        and isinstance(value.get("account_ref"), str)
        and bool(value["account_ref"])
        and isinstance(value.get("profile_id"), str)
        and bool(value["profile_id"])
        and type(value.get("revision")) is int
    ]
    return tuple(sorted(profiles, key=lambda value: (
        str(value.get("label", "")), str(value["profile_id"]),
    )))


def operation_availability(context: dict[str, Any]) -> dict[str, bool]:
    selected = context.get("selected_profile")
    status = context.get("status")
    specs = context.get("specs")
    if not isinstance(selected, dict) or not isinstance(status, dict) or not isinstance(specs, list):
        return {"start": False, "stop": False, "resume": False}
    selected_spec = str(context.get("selected_spec_id", ""))
    ready = any(
        isinstance(row, dict)
        and isinstance(row.get("spec"), dict)
        and row["spec"].get("spec_id") == selected_spec
        and row.get("readiness") == "READY"
        for row in specs
    )
    control = status.get("control")
    desired = control.get("desired_state") if isinstance(control, dict) else None
    active_spec = str(control.get("active_spec_id", "")) if isinstance(control, dict) else ""
    automatic = status.get("runtime_mode") == "automatic"
    return {
        "start": ready and status.get("runner") is None and desired is None,
        "stop": automatic and desired == "RUNNING" and active_spec == selected_spec,
        "resume": desired == "STOPPED" and active_spec == selected_spec,
    }


class MockAutomationWorker(QThread):
    completed = Signal(str, dict)
    failed = Signal(str, str)

    def __init__(
        self,
        content_client: CentralContentClient,
        credentials_client: CentralCredentialsClient,
        operation: str,
        values: dict[str, Any] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._content = content_client
        self._credentials = credentials_client
        self._operation = operation
        self._values = dict(values or {})

    def run(self) -> None:
        try:
            if self._operation == "publish_spec":
                self._content.publish_mock_automation_spec(**self._values)
            elif self._operation != "refresh":
                method = getattr(self._content, f"{self._operation}_mock_automation")
                method(**self._values)
            context = self._load_context(
                str(self._values.get("credential_profile_id", "")),
                str(self._values.get("spec_id", "")),
            )
            self.completed.emit(self._operation, context)
        except (RuntimeError, ValueError, OSError) as error:
            self.failed.emit(self._operation, str(error))

    def _load_context(self, preferred_profile: str, preferred_spec: str) -> dict[str, Any]:
        profiles = available_mock_profiles(self._credentials.load())
        selected = next(
            (value for value in profiles if value["profile_id"] == preferred_profile),
            profiles[0] if profiles else None,
        )
        if selected is None:
            return {"profiles": [], "selected_profile": None, "specs": [], "status": None}
        account_ref = str(selected["account_ref"])
        account_settings = self._credentials.account_settings(account_ref).get("settings", {})
        candidates_document = self._content.load_mock_automation_candidates(
            account_ref, credential_profile_id=str(selected["profile_id"]),
        )
        candidates = candidates_document.get("candidates", [])
        binding = candidates_document.get("binding")
        if not isinstance(candidates, list) or not isinstance(binding, dict):
            raise RuntimeError("NAS 후보 목록 형식이 올바르지 않습니다.")
        shadow_events = self._load_shadow_events()
        specs_document = self._content.load_mock_automation_specs(account_ref)
        specs = specs_document.get("specs", [])
        if not isinstance(specs, list):
            raise RuntimeError("NAS 운용 명세 목록 형식이 올바르지 않습니다.")
        ready_ids = [
            str(row["spec"]["spec_id"])
            for row in specs
            if isinstance(row, dict) and isinstance(row.get("spec"), dict)
            and row.get("readiness") == "READY" and row["spec"].get("spec_id")
        ]
        selected_spec = preferred_spec if preferred_spec in ready_ids else (
            ready_ids[-1] if ready_ids else ""
        )
        try:
            status = self._content.load_mock_automation_status(
                account_ref, credential_profile_id=str(selected["profile_id"]),
            )
        except RuntimeError as error:
            status = {"runtime_mode": None, "control": None, "runner": None,
                      "restore_error": str(error)}
        control = status.get("control")
        active_spec = str(control.get("active_spec_id", "")) if isinstance(control, dict) else ""
        if active_spec and any(
            isinstance(row, dict) and isinstance(row.get("spec"), dict)
            and row["spec"].get("spec_id") == active_spec for row in specs
        ):
            selected_spec = active_spec
        return {
            "profiles": list(profiles), "selected_profile": selected,
            "account_settings": account_settings, "specs": specs,
            "selected_spec_id": selected_spec, "status": status,
            "candidate_publications": candidates, "candidate_binding": binding,
            "shadow_events": shadow_events,
        }

    def _load_shadow_events(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        cursor = 0
        for _ in range(100):
            page = self._content.load_candidate_events(after_sequence=cursor, limit=1000)
            rows = page.get("events", [])
            if not isinstance(rows, list):
                raise RuntimeError("NAS Shadow 후보 목록 형식이 올바르지 않습니다.")
            values.extend(value for value in rows if isinstance(value, dict))
            if not page.get("has_more") or page.get("next_cursor") is None:
                return values
            next_cursor = int(page["next_cursor"])
            if next_cursor <= cursor:
                raise RuntimeError("NAS Shadow 후보 cursor가 전진하지 않았습니다.")
            cursor = next_cursor
        raise RuntimeError("NAS Shadow 후보가 100,000건을 초과했습니다.")


_FORWARD_INPUTS = (
    ("minimum_comparable_observations", "비교 관측 최소", 100, 0, 2_000_000_000),
    ("minimum_coverage_ppm", "자료 충족률 최소(ppm)", 990_000, 0, 1_000_000),
    ("maximum_p95_delay_ms", "p95 지연 최대(ms)", 2_000, 0, 2_000_000_000),
    ("maximum_gap_count", "자료 공백 최대", 1, 0, 2_000_000_000),
    ("maximum_submission_unknown_count", "주문결과 불명 최대", 0, 0, 2_000_000_000),
    ("maximum_rejected_order_count", "주문 거절 최대", 1, 0, 2_000_000_000),
    ("maximum_cancel_failure_count", "취소 실패 최대", 0, 0, 2_000_000_000),
    ("maximum_reconnect_count", "재연결 최대", 2, 0, 2_000_000_000),
    ("maximum_balance_mismatch_count", "잔고 불일치 최대", 0, 0, 2_000_000_000),
    ("minimum_active_day_count", "운용일 최소", 5, 0, 2_000_000_000),
    ("minimum_closed_trade_count", "완료 거래 최소", 10, 0, 2_000_000_000),
    ("minimum_adjusted_net_pnl_won", "조정 순손익 최소(원)", 0, -2_000_000_000, 2_000_000_000),
    ("maximum_drawdown_ppm", "낙폭 최대(ppm)", 100_000, 0, 1_000_000),
    ("maximum_exposure_ppm", "노출 최대(ppm)", 500_000, 0, 1_000_000),
)

_LIMIT_INPUTS = (
    ("maximum_concurrent_strategies", "동시 전략 최대", 1, 1, 100),
    ("maximum_concurrent_positions", "동시 보유 최대", 1, 1, 100),
    ("maximum_capital_won", "투입 자금 최대(원)", 10_000_000, 1, 2_000_000_000),
    ("maximum_daily_loss_won", "일 손실 최대(원)", 300_000, 1, 2_000_000_000),
    ("maximum_data_gap_seconds", "자료 공백 최대(초)", 5, 0, 86_400),
    ("maximum_submission_unknown_count", "주문결과 불명 최대", 0, 0, 1_000_000),
    ("maximum_reconnect_count", "재연결 최대", 2, 0, 1_000_000),
    ("maximum_balance_mismatch_count", "잔고 불일치 최대", 0, 0, 1_000_000),
)


class MockAutomationPublicationDialog(QDialog):
    def __init__(
        self, candidate: dict[str, Any], binding: dict[str, Any], profile_id: str,
        shadow_events: tuple[dict[str, Any], ...], parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("READY 모의운용 명세 작성")
        self.resize(650, 760)
        self._candidate = candidate
        self._binding = binding
        self._profile_id = profile_id
        self._shadow_events = shadow_events
        self._forward_inputs: dict[str, QSpinBox] = {}
        self._limit_inputs: dict[str, QSpinBox] = {}

        content = QWidget()
        layout = QVBoxLayout(content)
        notice = QLabel(
            "아래 값은 게시되는 고정 기준입니다. 제안값을 확인·수정한 뒤 게시하세요. "
            "게시만으로 주문은 시작되지 않습니다."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self._shadow = QComboBox()
        for event in shadow_events:
            self._shadow.addItem(
                f"{event.get('available_at', '')} · {event.get('symbol', '')}", event,
            )
        top = QFormLayout()
        top.addRow("Shadow 증거", self._shadow)
        self._start_delay = QSpinBox(); self._start_delay.setRange(1, 10_080); self._start_delay.setValue(5); self._start_delay.setSuffix(" 분 후")
        self._evaluation_days = QSpinBox(); self._evaluation_days.setRange(1, 365); self._evaluation_days.setValue(7); self._evaluation_days.setSuffix(" 일")
        top.addRow("평가 시작", self._start_delay)
        top.addRow("평가 기간", self._evaluation_days)
        layout.addLayout(top)
        layout.addWidget(self._input_group("Forward 통과 기준", _FORWARD_INPUTS, self._forward_inputs))
        layout.addWidget(self._input_group("모의 자동운용 한도", _LIMIT_INPUTS, self._limit_inputs))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("READY 명세 게시")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(content)
        outer = QVBoxLayout(self); outer.addWidget(scroll)

    @staticmethod
    def _input_group(title, definitions, target):
        group = QGroupBox(title)
        form = QFormLayout(group)
        for name, label, initial, minimum, maximum in definitions:
            field = QSpinBox(); field.setRange(minimum, maximum); field.setValue(initial)
            field.setGroupSeparatorShown(True)
            target[name] = field
            form.addRow(label, field)
        return group

    def publication_values(self) -> dict[str, Any]:
        frozen_at = datetime.now(timezone.utc)
        evaluation_start = frozen_at + timedelta(minutes=self._start_delay.value())
        evaluation_end = evaluation_start + timedelta(days=self._evaluation_days.value())
        criteria = ForwardCriteria(**{
            name: field.value() for name, field in self._forward_inputs.items()
        })
        limits = {name: field.value() for name, field in self._limit_inputs.items()}
        if set(limits) != set(MOCK_AUTOMATION_LIMIT_FIELDS):
            raise ValueError("모의 자동운용 한도 입력이 완전하지 않습니다.")
        event = self._shadow.currentData()
        if not isinstance(event, dict):
            raise ValueError("일치하는 Shadow 증거를 선택하세요.")
        return build_mock_automation_publication_documents(
            candidate_publication=self._candidate, binding=self._binding,
            credential_profile_id=self._profile_id, shadow_event=event,
            evaluation_start=evaluation_start, evaluation_end=evaluation_end,
            frozen_at=frozen_at, criteria=criteria, limits=limits,
        )


class MockAutomationDialog(QDialog):
    def __init__(
        self,
        content_client: CentralContentClient,
        credentials_client: CentralCredentialsClient,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("모의 자동운용")
        self.resize(680, 360)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._content = content_client
        self._credentials = credentials_client
        self._worker: MockAutomationWorker | None = None
        self._context: dict[str, Any] = {}

        self._profile = QComboBox()
        self._profile.currentIndexChanged.connect(self._profile_changed)
        self._spec = QComboBox()
        self._spec.currentIndexChanged.connect(self._spec_changed)
        self._candidate = QComboBox()
        self._account = QLabel("-")
        self._readiness = QLabel("READY 명세를 확인하는 중입니다…")
        self._readiness.setWordWrap(True)
        self._runtime = QLabel("-")
        self._runtime.setWordWrap(True)
        self._reason = QLineEdit()
        self._reason.setPlaceholderText("중지·재개 이유를 입력하세요")

        form = QFormLayout()
        form.addRow("모의계좌", self._profile)
        form.addRow("계좌 식별자", self._account)
        form.addRow("게시 후보", self._candidate)
        form.addRow("READY 명세", self._spec)
        form.addRow("명세 상태", self._readiness)
        form.addRow("운용 상태", self._runtime)
        form.addRow("작업 이유", self._reason)

        self._refresh = QPushButton("새로 확인")
        self._publish = QPushButton("새 READY 명세 작성")
        self._start = QPushButton("자동운용 시작")
        self._stop = QPushButton("신규 주문 중지")
        self._resume = QPushButton("자동운용 재개")
        self._refresh.clicked.connect(lambda: self._request("refresh"))
        self._publish.clicked.connect(self._publish_selected_candidate)
        self._start.clicked.connect(lambda: self._request("start"))
        self._stop.clicked.connect(lambda: self._request("stop"))
        self._resume.clicked.connect(lambda: self._request("resume"))
        buttons = QHBoxLayout()
        buttons.addWidget(self._refresh)
        buttons.addWidget(self._publish)
        buttons.addStretch()
        buttons.addWidget(self._start)
        buttons.addWidget(self._stop)
        buttons.addWidget(self._resume)

        self._message = QLabel(
            "이 화면은 모의계좌만 제어합니다. 중지는 기존 주문을 취소하거나 보유 종목을 자동 매도하지 않습니다."
        )
        self._message.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self._message)
        self._set_busy(False)
        self._window_settings = QSettings("KiwoomMonitor", "MockAutomationDialog")
        geometry = self._window_settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._context:
            self._request("refresh")

    def _profile_changed(self) -> None:
        if self._profile.signalsBlocked() or self._worker is not None:
            return
        self._request("refresh")

    def _spec_changed(self) -> None:
        if self._spec.signalsBlocked():
            return
        self._context["selected_spec_id"] = str(self._spec.currentData() or "")
        self._apply_button_state()

    def _request(self, operation: str) -> None:
        if self._worker is not None:
            return
        selected = self._selected_profile()
        spec_id = str(self._spec.currentData() or self._context.get("selected_spec_id", ""))
        values: dict[str, Any] = {}
        if selected is not None:
            values.update({
                "account_ref": str(selected["account_ref"]),
                "credential_profile_id": str(selected["profile_id"]),
                "spec_id": spec_id,
            })
        if operation != "refresh":
            if selected is None or not spec_id:
                self._message.setText("모의계좌와 READY 명세를 먼저 선택하세요.")
                return
            settings = self._context.get("account_settings", {})
            if operation in {"start", "resume"}:
                values["expected_settings_revision"] = int(settings.get("revision", -1))
                values["credential_revision"] = int(selected["revision"])
            if operation in {"stop", "resume"}:
                control = (self._context.get("status") or {}).get("control")
                if not isinstance(control, dict):
                    self._message.setText("현재 제어 revision을 다시 확인하세요.")
                    return
                values["expected_control_revision"] = int(control["control_revision"])
                reason = self._reason.text().strip()
                if not reason:
                    self._message.setText("중지·재개 이유를 입력하세요.")
                    return
                values["reason"] = reason
        self._set_busy(True)
        worker = MockAutomationWorker(
            self._content, self._credentials, operation, values, self,
        )
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(lambda source=worker: self._worker_finished(source))
        self._worker = worker
        worker.start()

    def _publish_selected_candidate(self) -> None:
        if self._worker is not None:
            return
        selected = self._selected_profile()
        candidate = self._candidate.currentData()
        binding = self._context.get("candidate_binding")
        if not isinstance(selected, dict) or not isinstance(candidate, dict) or not isinstance(binding, dict):
            self._message.setText("게시 가능한 모의계좌 후보를 먼저 선택하세요.")
            return
        events = matching_shadow_events(
            candidate,
            tuple(value for value in self._context.get("shadow_events", ()) if isinstance(value, dict)),
        )
        if not events:
            self._message.setText("이 후보 설정과 일치하는 실제 Shadow 후보 기록이 아직 없습니다.")
            return
        dialog = MockAutomationPublicationDialog(
            candidate, binding, str(selected["profile_id"]), events, self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            values = dialog.publication_values()
        except (TypeError, ValueError) as error:
            self._message.setText(f"READY 명세 작성 실패 · {error}")
            return
        self._set_busy(True)
        worker = MockAutomationWorker(
            self._content, self._credentials, "publish_spec", values, self,
        )
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(lambda source=worker: self._worker_finished(source))
        self._worker = worker
        worker.start()

    def _completed(self, operation: str, context: dict[str, Any]) -> None:
        self._context = context
        self._apply_context()
        messages = {
            "refresh": "NAS의 최신 모의 자동운용 상태를 확인했습니다.",
            "start": "모의 자동운용 시작 요청이 반영됐습니다.",
            "stop": "신규 자동 주문을 중지했습니다.",
            "resume": "모의 자동운용 재개 요청이 반영됐습니다.",
            "publish_spec": "READY 모의운용 명세를 게시했습니다. 주문은 아직 시작되지 않았습니다.",
        }
        self._message.setText(messages.get(operation, "완료"))

    def _failed(self, _operation: str, message: str) -> None:
        self._message.setText(f"NAS 자동운용 요청 실패 · {message}")

    def _worker_finished(self, worker: MockAutomationWorker) -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
        self._set_busy(False)

    def _selected_profile(self) -> dict[str, Any] | None:
        profile_id = str(self._profile.currentData() or "")
        return next((
            value for value in self._context.get("profiles", ())
            if isinstance(value, dict) and value.get("profile_id") == profile_id
        ), self._context.get("selected_profile"))

    def _apply_context(self) -> None:
        selected = self._context.get("selected_profile")
        profiles = self._context.get("profiles", ())
        selected_profile_id = str(selected.get("profile_id", "")) if isinstance(selected, dict) else ""
        self._profile.blockSignals(True)
        self._profile.clear()
        for profile in profiles:
            label = str(profile.get("label") or profile["profile_id"])
            self._profile.addItem(label, profile["profile_id"])
        self._profile.setCurrentIndex(max(0, self._profile.findData(selected_profile_id)))
        self._profile.blockSignals(False)
        self._account.setText(str(selected.get("account_ref", "-")) if isinstance(selected, dict) else "-")

        self._candidate.clear()
        for publication in self._context.get("candidate_publications", ()):
            if not isinstance(publication, dict):
                continue
            package = publication.get("package")
            receipt = publication.get("eligibility_receipt")
            if not isinstance(package, dict) or not isinstance(receipt, dict):
                continue
            status = str(receipt.get("status", "BLOCKED"))
            if status != "ELIGIBLE":
                continue
            source = package.get("source_final", {})
            finished = source.get("finished_at", "") if isinstance(source, dict) else ""
            self._candidate.addItem(
                f"{package.get('strategy_ref', '')} · {status} · {finished}", publication,
            )

        selected_spec_id = str(self._context.get("selected_spec_id", ""))
        self._spec.blockSignals(True)
        self._spec.clear()
        for row in self._context.get("specs", ()):
            if not isinstance(row, dict) or not isinstance(row.get("spec"), dict):
                continue
            spec = row["spec"]
            label = f"{spec.get('strategy_ref', '')} · {row.get('readiness', 'BLOCKED')}"
            self._spec.addItem(label, spec.get("spec_id", ""))
        index = self._spec.findData(selected_spec_id)
        self._spec.setCurrentIndex(index if index >= 0 else 0)
        self._spec.blockSignals(False)
        self._context["selected_spec_id"] = str(self._spec.currentData() or "")

        selected_row = next((
            row for row in self._context.get("specs", ())
            if isinstance(row, dict) and isinstance(row.get("spec"), dict)
            and row["spec"].get("spec_id") == self._context["selected_spec_id"]
        ), None)
        if selected_row is None:
            self._readiness.setText("게시된 운용 명세가 없습니다.")
        else:
            reasons = ", ".join(str(value) for value in selected_row.get("reasons", ()))
            self._readiness.setText(str(selected_row.get("readiness", "BLOCKED")) + (
                f" · {reasons}" if reasons else ""
            ))
        status = self._context.get("status")
        if isinstance(status, dict):
            control = status.get("control")
            desired = control.get("desired_state") if isinstance(control, dict) else "미시작"
            runner = status.get("runner")
            runner_state = runner.get("state") if isinstance(runner, dict) else "없음"
            error = str(status.get("restore_error", ""))
            latency = runner.get("latency") if isinstance(runner, dict) else None
            latency_text = ""
            if isinstance(latency, dict):
                latency_text = (
                    f" · 최근 조회 {latency.get('observation_count', 0)}건/"
                    f"{latency.get('last_poll_duration_ms', 0)}ms"
                    f" (DB {latency.get('load_duration_ms', 0)}ms)"
                )
                if latency.get("last_bar_age_ms") is not None:
                    processed_at = str(latency.get("last_bar_processed_at") or "")
                    latency_text += (
                        f" · 마지막 분봉 가용→처리 {latency['last_bar_age_ms']}ms"
                        f" ({processed_at[11:19]} UTC)"
                    )
                if latency.get("future_or_invalid_bar_time_count"):
                    latency_text += " · 분봉 시각 확인 필요"
            self._runtime.setText(
                f"모드 {status.get('runtime_mode') or '수동'} · 제어 {desired} · runner {runner_state}"
                + (f" · 복원 오류 {error}" if error else "")
                + latency_text
            )
        else:
            self._runtime.setText("상태를 확인할 수 없습니다.")
        self._apply_button_state()

    def _apply_button_state(self) -> None:
        state = operation_availability(self._context)
        self._start.setEnabled(state["start"] and self._worker is None)
        self._stop.setEnabled(state["stop"] and self._worker is None)
        self._resume.setEnabled(state["resume"] and self._worker is None)

    def _set_busy(self, busy: bool) -> None:
        self._refresh.setEnabled(not busy)
        self._profile.setEnabled(not busy)
        self._spec.setEnabled(not busy)
        self._candidate.setEnabled(not busy)
        self._publish.setEnabled(not busy and self._candidate.count() > 0)
        if busy:
            self._start.setEnabled(False)
            self._stop.setEnabled(False)
            self._resume.setEnabled(False)
        else:
            self._apply_button_state()

    def stop(self) -> None:
        self._window_settings.setValue("geometry", self.saveGeometry())
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(12_000)

    def closeEvent(self, event) -> None:
        self._window_settings.setValue("geometry", self.saveGeometry())
        event.ignore()
        self.hide()
