from __future__ import annotations

from kiwoom_monitor.domain.time_basis import kst_now
from kiwoom_monitor.infrastructure.time_display import format_kst_time

from datetime import datetime, timedelta, timezone
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from kiwoom_monitor.application.mock_automation_specification import (
    MOCK_AUTOMATION_LIMIT_FIELDS,
    build_mock_automation_publication_documents,
    matching_shadow_events,
)
from kiwoom_monitor.domain.execution_activation import (
    CLOSE_NATIVE_PLAN_MODE, CloseMockExecutionPolicy, ForwardCriteria, PANIC_NATIVE_PLAN_MODE, PanicMockExecutionPolicy,
    LEADER_NATIVE_PLAN_MODE, MockUnfilledExecutionPolicy,
    MockAutomationAccountLimits, MockAutomationAccountSelection,
    MockAutomationAccountSelectionV2, MockAutomationRetainedAdmissionRef,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION
from kiwoom_monitor.domain.strategy_contract import (
    LEADER_BREAKOUT_FAMILY_ID, leader_execution_contract_from_dict,
)
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient
from kiwoom_monitor.presentation.mock_automation_candidate_dialog import MockAutomationCandidateDialog


def _minute_research_family(publication: Any) -> str:
    package = publication.get('package') if isinstance(publication, dict) else None
    spec = package.get('candidate_spec') if isinstance(package, dict) else None
    if not isinstance(spec, dict) or spec.get('version') != CLOSE_FINAL_CANDIDATE_VERSION:
        return ''
    family = spec.get('family')
    if family is None: family = 'close_betting/v1'
    return family if family in {'close_betting/v1', 'ipo_close/v1', 'ipo_breakout/v1', 'panic_rebound/v1'} else ''


def _minute_research_label(publication: Any) -> str:
    return {'close_betting/v1': '종가베팅', 'ipo_close/v1': '신규주 종가베팅',
            'ipo_breakout/v1': '신규주 돌파', 'panic_rebound/v1': '낙주'}.get(_minute_research_family(publication), '')


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


def account_preparation_reasons(context: dict[str, Any]) -> tuple[str, ...]:
    selected = context.get("selected_profile")
    settings = context.get("account_settings")
    if not isinstance(selected, dict) or not isinstance(settings, dict):
        return ("모의계좌 설정을 확인할 수 없습니다.",)
    if (
        not selected.get("account_ref") or not selected.get("profile_id")
        or settings.get("scope") != {
            "broker": "kiwoom", "environment": "mock", "account_ref": selected["account_ref"],
        }
        or settings.get("active_profile_id") != selected["profile_id"]
        or type(settings.get("revision")) is not int or settings["revision"] < 0
    ):
        return ("선택한 모의계좌의 활성 프로필과 설정 버전을 다시 확인하세요.",)
    reasons = []
    if settings.get("monitor_enabled") is not True:
        reasons.append("모의계좌 감시가 꺼져 있거나 확인되지 않았습니다.")
    if settings.get("mock_order_enabled") is not True:
        reasons.append("모의주문 허용이 꺼져 있습니다. 계좌 설정을 확인하세요.")
    return tuple(reasons)


def selected_account_selection(context: dict[str, Any]) -> dict[str, Any] | None:
    identity = context.get("selected_account_selection_id")
    return next((value for value in context.get("account_selections", ())
                 if isinstance(value, dict) and value.get("selection_id") == identity), None)


def operation_availability(context: dict[str, Any]) -> dict[str, bool]:
    selected = context.get("selected_profile")
    status = context.get("status")
    specs = context.get("specs")
    if not isinstance(selected, dict) or not isinstance(status, dict) or not isinstance(specs, list):
        return {"start": False, "stop": False, "resume": False}
    if status.get("status_query_error"):
        return {"start": False, "stop": False, "resume": False}
    selected_spec = str(context.get("selected_spec_id", ""))
    ready = any(
        isinstance(row, dict)
        and isinstance(row.get("spec"), dict)
        and row["spec"].get("spec_id") == selected_spec
        and row.get("readiness") == "READY"
        for row in specs
    )
    selection = selected_account_selection(context)
    if context.get("selected_account_selection_id"):
        members = selection.get("member_spec_ids", []) if selection else []
        order = selection.get("execution_order_spec_ids", []) if selection else []
        binding = context.get("candidate_binding", {})
        try:
            same_verified_at = bool(selection) and datetime.fromisoformat(
                selection["binding_verified_at"]) == datetime.fromisoformat(binding["verified_at"])
        except (KeyError, TypeError, ValueError):
            same_verified_at = False
        ready_ids = {row["spec"].get("spec_id") for row in specs if isinstance(row, dict)
                     and isinstance(row.get("spec"), dict) and row.get("readiness") == "READY"}
        ready = bool(selection and members and order and len(order) == len(members)
                     and len(set(order)) == len(order) and set(order) == set(members)
                     and set(members) <= ready_ids and members[0] == selected_spec
                     and selection.get("credential_profile_id") == selected.get("profile_id")
                     and selection.get("account_scope", {}).get("account_ref") == selected.get("account_ref")
                     and selection.get("binding_revision") == binding.get("binding_revision")
                     and same_verified_at)
    control = status.get("control")
    desired = control.get("desired_state") if isinstance(control, dict) else None
    active_spec = str(control.get("active_spec_id", "")) if isinstance(control, dict) else ""
    automatic = status.get("runtime_mode") == "AUTOMATIC"
    account_ready = not account_preparation_reasons(context)
    return {
        "start": (
            account_ready and ready and status.get("runner") is None and desired is None
            and not status.get("restore_error")
        ),
        "stop": automatic and desired == "RUNNING" and active_spec == selected_spec,
        "resume": account_ready and active_spec == selected_spec and (
            desired == "STOPPED" or desired == "RUNNING" and control.get("entry_resume_required", False)
        ),
    }


def replacement_available(context):
    status = context.get('status') or {}
    control, runner = status.get('control') or {}, status.get('runner') or {}
    active, desired = control.get('account_selection') or {}, selected_account_selection(context) or {}
    applied_matches = (active.get('selection_id') == runner.get('account_selection_id') and active.get('selection_id'))
    if not active:
        applied_matches = (control.get('active_spec_id') and (runner.get('spec_id'), runner.get('execution_run_id'))
                           == (control.get('active_spec_id'), control.get('execution_run_id')))
    return bool(not account_preparation_reasons(context) and not status.get('status_query_error')
        and status.get('runtime_mode') == 'AUTOMATIC' and type(control.get('control_revision')) is int
        and applied_matches
        and desired.get('version') == 'mock_automation_account_selection/v2'
        and desired.get('selection_id') != active.get('selection_id')
        and desired.get('member_spec_ids') and desired['member_spec_ids'][0] == control.get('active_spec_id'))


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
        command_acknowledged = False
        try:
            if self._operation in {'selected_status', 'selected_prepare', 'selected_accept', 'selected_stop'}:
                operation_id = self._values['operation_id']
                result = None
                if self._operation != 'selected_status':
                    if self._operation == 'selected_stop':
                        result = self._content.stop_mock_automation(**{key: value for key, value in self._values.items()
                            if key != 'operation_id'})
                    else:
                        method = (self._content.prepare_selected_order_management if self._operation == 'selected_prepare'
                                  else self._content.accept_selected_order_management)
                        result = method(**self._values)
                    command_acknowledged = True
                status = self._content.load_selected_order_management(operation_id=operation_id)
                self.completed.emit(self._operation, {'selected_management': status, 'acknowledged': result})
                return
            if self._operation == "publish_spec":
                self._content.publish_mock_automation_spec(**self._values)
                command_acknowledged = True
            elif self._operation == "publish_account_selection":
                self._content.publish_mock_automation_account_selection(**self._values)
                command_acknowledged = True
            elif self._operation not in {"refresh", "prepare_publication"}:
                method = getattr(self._content, f"{self._operation}_mock_automation")
                method(**self._values)
                command_acknowledged = True
            context = self._load_context(
                str(self._values.get("credential_profile_id", "")),
                str(self._values.get("spec_id", "")),
                str(self._values.get("account_selection_id", "") or
                    (self._values.get("selection") or {}).get("selection_id", "")),
            )
            if self._operation == 'refresh' and self._values.get('management_operation_id'):
                context['selected_management'] = self._content.load_selected_order_management(
                    operation_id=self._values['management_operation_id'])
            if self._operation == "prepare_publication":
                selected = context.get("selected_profile")
                if not isinstance(selected, dict) or (
                    selected.get("profile_id") != self._values.get("credential_profile_id")
                    or selected.get("account_ref") != self._values.get("account_ref")
                ):
                    raise RuntimeError("선택한 모의계좌와 프로필을 다시 확인하세요.")
                candidate = next((value for value in context.get("candidate_publications", ())
                    if value == self._values.get("candidate_publication")), None)
                if candidate is None:
                    raise RuntimeError("선택한 후보 문서가 변경됐습니다. 새로 확인 후 다시 선택하세요.")
                context["publication_candidate"] = candidate
                context["shadow_events"] = self._load_shadow_events()
                if self.isInterruptionRequested():
                    return
            self.completed.emit(self._operation, context)
        except (RuntimeError, ValueError, OSError) as error:
            if command_acknowledged:
                self.completed.emit(self._operation, {
                    "context_refresh_error": str(error),
                    "status": {"status_query_error": str(error) or "상태 미확인"},
                })
            else:
                self.failed.emit(self._operation, str(error))

    def _load_context(self, preferred_profile: str, preferred_spec: str,
                      preferred_selection: str = "") -> dict[str, Any]:
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
        specs_document = self._content.load_mock_automation_specs(account_ref, include_admission_refs=True)
        specs = specs_document.get("specs", [])
        if not isinstance(specs, list):
            raise RuntimeError("NAS 운용 명세 목록 형식이 올바르지 않습니다.")
        selection_document = self._content.load_mock_automation_account_selections(
            account_ref, credential_profile_id=str(selected["profile_id"]),
        )
        selections = selection_document.get("selections")
        if not isinstance(selections, list):
            raise RuntimeError("계좌 구성 목록 형식이 올바르지 않습니다.")
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
                      "status_query_error": str(error)}
        control = status.get("control")
        active_spec = str(control.get("active_spec_id", "")) if isinstance(control, dict) else ""
        runner = status.get("runner")
        active_composition = control.get("account_selection") if isinstance(control, dict) else None
        active_selection = str(active_composition.get("selection_id", "")) if isinstance(active_composition, dict) else ""
        if not active_selection and isinstance(runner, dict):
            active_selection = str(runner.get("account_selection_id", ""))
        selected_selection = active_selection or preferred_selection
        selection = next((value for value in selections if isinstance(value, dict)
                          and value.get("selection_id") == selected_selection), None)
        if selection and selection.get("member_spec_ids"):
            selected_spec = selection["member_spec_ids"][0]
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
            "account_selections": selections, "selected_account_selection_id": selected_selection,
        }

    def _load_shadow_events(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        cursor = 0
        for _ in range(100):
            if self.isInterruptionRequested():
                return []
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


class MockAutomationAccountSelectionDialog(QDialog):
    def __init__(self, specs: list[dict[str, Any]], binding: dict[str, Any],
                 profile_id: str, *, now_provider, selection=None, current_selection=None, admission_refs=(),
                 original_anchor=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("함께 운용할 명세와 공통 한도")
        self.resize(720, 680)
        self._binding, self._profile_id = binding, profile_id
        self._now_provider = now_provider
        self._member_inputs: dict[str, tuple[QCheckBox, QLineEdit]] = {}
        self._limit_inputs: dict[str, QLineEdit] = {}
        saved = selection or {}
        self._current_selection = current_selection
        self._admission_refs = tuple(MockAutomationRetainedAdmissionRef(**value) for value in admission_refs)
        self._replacement = current_selection is not None or bool(self._admission_refs)
        self._stored_admission_refs = {row['spec']['spec_id']: row['admission_ref'] for row in specs
            if isinstance(row, dict) and isinstance(row.get('spec'), dict) and isinstance(row.get('admission_ref'), dict)}
        retained_ids = {value.spec_id for value in self._admission_refs}
        saved_members = saved.get("member_spec_ids", [])
        saved_entries = saved.get('entry_spec_ids', saved_members)
        saved_order = saved.get("execution_order_spec_ids", [])
        content = QWidget()
        layout = QVBoxLayout(content)
        notice = QLabel("함께 운용할 명세를 선택하고 실행 순서와 공통 한도를 입력하세요. "
                        "계좌 기준 명세는 계좌 제어의 기준이며 매매 우선순위는 실행 순서를 따릅니다. "
                        "저장한 뒤 별도로 시작해야 적용됩니다.")
        notice.setWordWrap(True)
        if self._replacement:
            notice.setText('새로 진입할 명세를 선택하세요. 기존 명세는 선택을 해제해도 원래 주문·보유·보호 관리를 계속합니다. '
                           '모든 명세의 실행 순서와 공통 한도를 저장한 뒤 별도로 교체를 적용하세요. 중지·재개 상태는 유지됩니다.')
        layout.addWidget(notice)
        members = QGroupBox("운용 명세 · 실행 순서")
        members_form = QFormLayout(members)
        self._anchor = QComboBox()
        self._anchor.addItem("계좌 기준 명세를 선택하세요", None)
        for row in specs:
            spec = row.get("spec") if isinstance(row, dict) else None
            if not isinstance(spec, dict) or (row.get("readiness") != "READY" and spec.get('spec_id') not in retained_ids):
                continue
            identity = str(spec.get("spec_id", ""))
            label = str(spec.get("strategy_ref") or identity)
            checked = QCheckBox(label)
            checked.setToolTip(identity)
            checked.setChecked(identity in saved_entries and row.get('readiness') == 'READY')
            if identity in retained_ids:
                checked.setText(label + ' · 기존 관리 유지 · 선택 시 신규 진입')
                checked.setEnabled(row.get('readiness') == 'READY')
            priority = QLineEdit()
            priority.setPlaceholderText("실행 순서 · 1부터 입력")
            if identity in saved_order:
                priority.setText(str(saved_order.index(identity) + 1))
            priority.setMaximumWidth(180)
            self._member_inputs[identity] = checked, priority
            line = QHBoxLayout()
            line.addWidget(checked, 1)
            line.addWidget(priority)
            members_form.addRow(line)
            individual = " · ".join(f"{text} {spec[field]:,}{unit}" for field, text, unit in (
                ("maximum_concurrent_positions", "동시 보유", "개"),
                ("maximum_capital_won", "투입 자금", "원"),
                ("maximum_daily_loss_won", "일 손실", "원"),
                ("maximum_data_gap_seconds", "자료 공백", "초"),
            ) if type(spec.get(field)) is int)
            if individual:
                description = QLabel("개별 한도 · " + individual)
                description.setWordWrap(True)
                members_form.addRow(description)
            self._anchor.addItem(label, identity)
        if saved_members:
            self._anchor.setCurrentIndex(max(0, self._anchor.findData(saved_members[0])))
        if self._replacement:
            anchor = original_anchor or current_selection['member_spec_ids'][0]
            self._anchor.setCurrentIndex(self._anchor.findData(anchor))
            self._anchor.setEnabled(False)
        members_form.addRow("계좌 기준 명세", self._anchor)
        layout.addWidget(members)
        limits = QGroupBox("계좌 공통 한도")
        limits_form = QFormLayout(limits)
        for name, label, *_unused in _LIMIT_INPUTS:
            field = QLineEdit()
            field.setPlaceholderText("정수로 직접 입력하세요")
            if name in saved.get("limits", {}):
                field.setText(str(saved["limits"][name]))
            self._limit_inputs[name] = field
            limits_form.addRow(label, field)
        layout.addWidget(limits)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self._error = QLabel()
        self._error.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("계좌 구성 저장")
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll)
        outer.addWidget(self._error)
        outer.addWidget(buttons)
        self._prepared_values = None

    def publication_values(self) -> dict[str, Any]:
        members, priorities = [], {}
        for identity, (checked, priority) in self._member_inputs.items():
            if not checked.isChecked() and identity not in {ref.spec_id for ref in self._admission_refs}:
                continue
            value = priority.text().strip()
            if not value.isascii() or not value.isdigit():
                raise ValueError("선택한 명세의 실행 순서를 모두 입력하세요.")
            rank = int(value)
            if rank in priorities:
                raise ValueError("실행 순서는 중복 없이 입력하세요.")
            priorities[rank] = identity
            members.append(identity)
        anchor = self._anchor.currentData()
        if not members or anchor not in members:
            raise ValueError("운용할 명세와 선택한 명세 중 계좌 기준 명세를 선택하세요.")
        if set(priorities) != set(range(1, len(members) + 1)):
            raise ValueError("실행 순서는 1부터 선택한 명세 수까지 빠짐없이 입력하세요.")
        values = {}
        for name, field in self._limit_inputs.items():
            text_value = field.text().strip()
            if not text_value.isascii() or not text_value.isdigit():
                raise ValueError("계좌 공통 한도를 모두 정수로 입력하세요.")
            values[name] = int(text_value)
        binding = self._binding
        refs = list(self._admission_refs)
        entries = tuple(identity for identity, (checked, _) in self._member_inputs.items() if checked.isChecked())
        if self._replacement:
            for identity in entries:
                stored = self._stored_admission_refs.get(identity)
                if stored is not None and identity not in {ref.spec_id for ref in refs}:
                    ref = MockAutomationRetainedAdmissionRef(**stored)
                    if ref.execution_run_id != refs[0].execution_run_id:
                        raise ValueError('선택한 명세는 다른 실행에 입장돼 있습니다. 원래 실행을 확인하세요.')
                    refs.append(ref)
        selection_type = MockAutomationAccountSelectionV2 if self._replacement else MockAutomationAccountSelection
        selection = selection_type(
            account_scope=AccountScope(broker="kiwoom", environment=AccountEnvironment.MOCK,
                                       account_ref=str(binding["account_ref"])),
            credential_profile_id=self._profile_id, binding_revision=binding["binding_revision"],
            binding_verified_at=datetime.fromisoformat(binding["verified_at"]),
            frozen_at=self._now_provider(), member_spec_ids=(anchor, *(value for value in members if value != anchor)),
            execution_order_spec_ids=tuple(priorities[rank] for rank in sorted(priorities)),
            limits=MockAutomationAccountLimits(**values),
            **(dict(entry_spec_ids=entries, retained_admission_refs=tuple(refs)) if self._replacement else {}),
        )
        return {"account_ref": binding["account_ref"], "credential_profile_id": self._profile_id,
                "expected_binding_revision": binding["binding_revision"], "selection": selection.to_dict()}

    def _accept_selection(self):
        try:
            self._prepared_values = self.publication_values()
        except (KeyError, TypeError, ValueError) as error:
            self._error.setText(str(error))
            return
        self.accept()


class MockAutomationPublicationDialog(QDialog):
    def __init__(
        self, candidate: dict[str, Any], binding: dict[str, Any], profile_id: str,
        shadow_events: tuple[dict[str, Any], ...], parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("READY 모의운용 명세 작성")
        self.resize(650, 760)
        package = candidate.get("package")
        candidate_spec = package.get("candidate_spec") if isinstance(package, dict) else None
        parameters = candidate_spec.get("parameters", {}) if isinstance(candidate_spec, dict) else {}
        contract = parameters.get("execution_contract") or {}
        staged = (isinstance(candidate_spec, dict) and candidate_spec.get("family") == LEADER_BREAKOUT_FAMILY_ID
                  and contract.get("version") in {'leader_breakout_execution/v2', 'leader_breakout_execution/v3'})
        self._candidate = deepcopy(candidate) if staged else candidate
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
        self._position_terms = None
        if staged:
            execution = leader_execution_contract_from_dict(contract)
            quantity = f"{execution.quantity:,}주" if execution.quantity is not None else "미정"
            capital = f"{execution.capital_won:,}원" if execution.capital_won is not None else "미정"
            terms = [f"최초 진입 {quantity} · 누적 매수금액 한도 {capital}",
                     "일부 청산해도 누적 매수금액 한도는 되살아나지 않습니다."]
            comparisons = {'observed_cross_above': '상향 돌파 후 원래 확인 조건 충족',
                           'price_at_or_above': '이상', 'price_at_or_below': '이하'}
            for index, step in enumerate(execution.position_steps or (), 1):
                action = '추가 매수' if step.action == 'ADD' else '일부 청산'
                terms.append(f"{index}. {action} {step.quantity:,}주 · {step.reference_price:,}원 {comparisons[step.trigger]}")
            if execution.position_steps is None:
                terms.append("추가·감축 단계 미정")
            if execution.version == 'leader_breakout_execution/v3':
                terms.append("이전 실제 전량 청산을 확인한 뒤 새 독립 셋업으로만 재진입합니다.")
                terms.append("같은 셋업의 시각·세대만 바꿔 재진입하지 않습니다.")
            terms.append("현재 조건을 다시 판단하며, 접수된 미체결 잔량은 정정을 우선합니다.")
            self._position_terms = QLabel('\n'.join(terms))
            self._position_terms.setTextFormat(Qt.TextFormat.PlainText)
            self._position_terms.setWordWrap(True)
            self._position_terms.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._position_terms.setToolTip('\n'.join(
                f"{step.step_id}: {step.reason_ref}" for step in execution.position_steps or ()))
            layout.addWidget(self._position_terms)
        self._shadow = QComboBox()
        for event in shadow_events:
            self._shadow.addItem(
                f"{format_kst_time(event.get('available_at'), missing='')} · {event.get('symbol', '')}", event,
            )
        top = QFormLayout()
        top.addRow("Shadow 증거", self._shadow)
        self._close_inputs = {}
        self._close_management = None
        if _minute_research_family(candidate):
            kind = _minute_research_label(candidate)
            for name, label in (("maximum_adverse_slippage_bps", kind + " 진입 가격 허용 범위(bp)"),
                                ("protective_slippage_bps", kind + " 청산 가격 허용 범위(bp)")):
                field = QLineEdit()
                field.setPlaceholderText("0~9999 정수 · 100bp = 1%")
                self._close_inputs[name] = field
                top.addRow(label, field)
            self._close_management = QComboBox()
            self._close_management.addItem("중지 후 관리 방식을 선택하세요", None)
            panic = _minute_research_family(candidate) == 'panic_rebound/v1'
            self._close_management.addItem("신규 주문 중지·대사만 유지", "reconcile_only/v1" if panic else "close-mock-execution-policy/v3")
            self._close_management.addItem("신규 진입 중지·원래 계획의 취소·청산 유지", "continue_original_plan/v1" if panic else "close-mock-execution-policy/v4")
            top.addRow(kind + " 중지 후 관리", self._close_management)
        self._plan_policy_path = None
        if isinstance(candidate_spec, dict) and candidate_spec.get("family") == LEADER_BREAKOUT_FAMILY_ID:
            self._plan_policy_path = QLineEdit()
            self._plan_policy_path.setPlaceholderText(
                "현재 조건 재판단·정정 정책 파일 필수" if staged else "계획형 0B 모의운용을 선택할 때 지정")
            self._plan_policy_path.setClearButtonEnabled(True)
            browse = QPushButton("정책 파일 선택")
            browse.clicked.connect(self._choose_plan_policy)
            policy_row = QHBoxLayout()
            policy_row.addWidget(self._plan_policy_path)
            policy_row.addWidget(browse)
            top.addRow("계획형 모의 정책", policy_row)
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

    def _choose_plan_policy(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "계획형 0B 모의 정책", "", "JSON (*.json)")
        if path:
            self._plan_policy_path.setText(path)

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
        frozen_at = kst_now()
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
        policy = None
        close_policy = None
        panic_policy = None
        if self._close_management is not None:
            version = self._close_management.currentData()
            if version is None or any(not field.text().strip().isascii()
                    or not field.text().strip().isdigit() for field in self._close_inputs.values()):
                raise ValueError(_minute_research_label(self._candidate) + " 가격 허용 범위와 중지 후 관리 방식을 명시하세요.")
            if _minute_research_family(self._candidate) == 'panic_rebound/v1':
                panic_policy = PanicMockExecutionPolicy(
                    **{name: int(field.text().strip()) for name, field in self._close_inputs.items()},
                    stopped_management=version)
            else:
                close_policy = CloseMockExecutionPolicy(
                    **{name: int(field.text().strip()) for name, field in self._close_inputs.items()},
                    entry_price_basis="acknowledged_0B_bounded_by_signal/v1",
                    entry_remainder_policy="cancel_at_entry_deadline/v1",
                    exit_price_basis="acknowledged_0B_bounded_slippage/v1", version=version,
                    stopped_management="continue_original_plan/v1" if version in {'close-mock-execution-policy/v2', 'close-mock-execution-policy/v4'} else None,
                    accepted_order_management='reassess_then_amend_price/v1' if version in {
                        'close-mock-execution-policy/v3', 'close-mock-execution-policy/v4'} else None,
                )
        policy_path = self._plan_policy_path.text().strip() if self._plan_policy_path is not None else ""
        if policy_path:
            try:
                policy = MockUnfilledExecutionPolicy(**json.loads(Path(policy_path).read_text(encoding="utf-8")))
            except (OSError, TypeError, ValueError) as error:
                raise ValueError(f"계획형 모의 정책을 읽을 수 없습니다. {error}") from error
        return build_mock_automation_publication_documents(
            candidate_publication=self._candidate, binding=self._binding,
            credential_profile_id=self._profile_id, shadow_event=event,
            evaluation_start=evaluation_start, evaluation_end=evaluation_end,
            frozen_at=frozen_at, criteria=criteria, limits=limits,
            unfilled_execution_policy=policy,
            execution_plan_mode=(PANIC_NATIVE_PLAN_MODE if panic_policy is not None else
                                 CLOSE_NATIVE_PLAN_MODE if close_policy is not None else
                                 LEADER_NATIVE_PLAN_MODE if policy is not None else None),
            close_execution_policy=close_policy,
            **({"panic_execution_policy": panic_policy} if panic_policy is not None else {}),
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
        self.resize(780, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._content = content_client
        self._credentials = credentials_client
        self._worker: MockAutomationWorker | None = None
        self._candidate_files_dialog: MockAutomationCandidateDialog | None = None
        self._context: dict[str, Any] = {}
        self._management_packet = None
        self._management_status = None

        self._profile = QComboBox()
        self._profile.currentIndexChanged.connect(self._profile_changed)
        self._spec = QComboBox()
        self._spec.currentIndexChanged.connect(self._spec_changed)
        self._selection = QComboBox()
        self._selection.currentIndexChanged.connect(self._selection_changed)
        self._edit_selection = QPushButton("구성 작성·수정")
        self._edit_selection.clicked.connect(self._prepare_account_selection)
        self._selection_details = QLabel()
        self._selection_details.setWordWrap(True)
        self._members_status = QLabel()
        self._members_status.setWordWrap(True)
        self._candidate = QComboBox()
        self._candidate_files = QPushButton("후보 준비·게시")
        self._candidate_files.clicked.connect(self._prepare_candidate_files)
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
        candidate_row = QHBoxLayout()
        candidate_row.addWidget(self._candidate)
        candidate_row.addWidget(self._candidate_files)
        form.addRow("게시 후보", candidate_row)
        form.addRow("READY 명세", self._spec)
        selection_row = QHBoxLayout()
        selection_row.addWidget(self._selection, 1)
        selection_row.addWidget(self._edit_selection)
        form.addRow("함께 운용할 구성", selection_row)
        form.addRow("구성 저장·적용", self._selection_details)
        form.addRow("명세 상태", self._readiness)
        form.addRow("운용 상태", self._runtime)
        form.addRow("명세별 상태", self._members_status)
        form.addRow("작업 이유", self._reason)
        self._management_file = QPushButton('보관 주문 파일 선택·확인')
        self._management_file.clicked.connect(self._select_management_packet)
        self._management_prepare = QPushButton('관리 준비')
        self._management_prepare.clicked.connect(lambda: self._request_selected_management('selected_prepare'))
        self._management_accept = QPushButton('현재 관리 수락')
        self._management_accept.clicked.connect(lambda: self._request_selected_management('selected_accept'))
        self._management_stop = QPushButton('관리 중지')
        self._management_stop.clicked.connect(lambda: self._request_selected_management('selected_stop'))
        self._management_label = QLabel('이 실행 컴퓨터에 설치한 보관 주문을 선택하세요. 신규 진입은 계속 꺼집니다.')
        self._management_label.setWordWrap(True)
        management_buttons = QHBoxLayout()
        management_buttons.addWidget(self._management_file)
        management_buttons.addWidget(self._management_prepare)
        management_buttons.addWidget(self._management_accept)
        management_buttons.addWidget(self._management_stop)
        form.addRow('보관 주문 관리', management_buttons)
        form.addRow('관리 준비·실행', self._management_label)

        self._refresh = QPushButton("새로 확인")
        self._publish = QPushButton("새 READY 명세 작성")
        self._start = QPushButton("자동운용 시작")
        self._stop = QPushButton("신규 주문 중지")
        self._resume = QPushButton("자동운용 재개")
        self._replace_selection = QPushButton('선택 구성으로 교체')
        self._refresh.clicked.connect(lambda: self._request("refresh"))
        self._publish.clicked.connect(self._publish_selected_candidate)
        self._candidate.currentIndexChanged.connect(self._update_candidate_action)
        self._start.clicked.connect(lambda: self._request("start"))
        self._stop.clicked.connect(lambda: self._request("stop"))
        self._resume.clicked.connect(lambda: self._request("resume"))
        self._replace_selection.clicked.connect(lambda: self._request('replace_selection'))
        buttons = QHBoxLayout()
        buttons.addWidget(self._refresh)
        buttons.addWidget(self._publish)
        buttons.addStretch()
        buttons.addWidget(self._start)
        buttons.addWidget(self._stop)
        buttons.addWidget(self._resume)
        buttons.addWidget(self._replace_selection)

        self._message = QLabel(
            "이 화면은 모의계좌만 제어합니다. 중지 후 관리는 게시한 운용 명세를 따릅니다. "
            "종가베팅 v2는 신규 진입을 막고 원래 계획의 취소·청산을 유지합니다."
        )
        self._message.setWordWrap(True)
        layout = QVBoxLayout(self)
        self._strategy_scope = QLabel("등록한 정책과 승인 명세로 모의계좌를 운용합니다. "
            "주도주 구조 돌파는 실제 0B·현재 근거·명시 실행 계약을 사용하며 미정 조건은 주문을 차단합니다. "
            "기존 완료봉 Family는 비교 정책으로 구분합니다.")
        self._strategy_scope.setWordWrap(True)
        layout.addWidget(self._strategy_scope)
        form_content = QWidget()
        form_content.setLayout(form)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(form_content)
        layout.addWidget(form_scroll)
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
        self._management_status = None
        self._request("refresh")

    def _select_management_packet(self):
        if self._worker is not None:
            return
        path, _ = QFileDialog.getOpenFileName(self, '설치한 보관 주문 자료 선택', '', '주문 자료 (*.json)')
        if not path:
            return
        self._management_packet, self._management_status = None, None
        try:
            from kiwoom_monitor.domain.selected_order_evidence import (
                MANAGEMENT_VERSION, PLAN_MANAGEMENT_VERSION, encode_selected_order_evidence)
            packet = json.loads(Path(path).read_text(encoding='utf-8'))
            _, digest = encode_selected_order_evidence(packet)
            if packet['version'] not in {MANAGEMENT_VERSION, PLAN_MANAGEMENT_VERSION}:
                raise ValueError('원래 운용 정책이 포함된 보관 주문 자료를 선택하세요.')
            self._management_packet = dict(operation_id=packet['operation_id'], packet_sha256=digest,
                account_ref=packet['account_ref'], symbol=packet['order']['symbol'])
            self._management_status = None
            self._start_worker('selected_status', {'operation_id': packet['operation_id']})
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._show_selected_management()
            self._management_label.setText(str(error))

    def _request_selected_management(self, operation):
        if self._worker is not None:
            return
        packet, status = self._management_packet, self._management_status
        selected, binding = self._selected_profile(), self._context.get('candidate_binding')
        if not packet or not status or not selected or not isinstance(binding, dict):
            self._management_label.setText('보관 주문과 현재 모의계좌를 다시 확인하세요.')
            return
        if operation == 'selected_stop':
            context = (status.get('management') or {}).get('context') or {}
            reason = self._reason.text().strip()
            if not reason or not context:
                self._management_label.setText('관리 중지 이유를 입력하세요.')
                return
            self._start_worker(operation, dict(operation_id=packet['operation_id'],
                credential_profile_id=selected['profile_id'], account_ref=context['account_ref'],
                spec_id=context['spec_id'], expected_control_revision=status['control']['control_revision'], reason=reason))
            return
        values = dict(operation_id=packet['operation_id'], packet_sha256=packet['packet_sha256'],
            credential_profile_id=selected['profile_id'], credential_revision=selected['revision'],
            account_ref=selected['account_ref'], binding_revision=binding['binding_revision'],
            expected_settings_revision=self._context['account_settings']['revision'])
        if operation == 'selected_accept':
            values['expected_control_revision'] = status['control']['control_revision']
        self._start_worker(operation, values)

    def _show_selected_management(self):
        status, packet = self._management_status or {}, self._management_packet or {}
        selected, binding = self._selected_profile() or {}, self._context.get('candidate_binding') or {}
        installation, management = status.get('installation') or {}, status.get('management') or {}
        context, control = management.get('context') or {}, status.get('control') or {}
        settings = self._context.get('account_settings') or {}
        matched = bool(packet and status.get('operation_id') == packet.get('operation_id')
            and status.get('packet_sha256') == packet.get('packet_sha256')
            and status.get('account_ref') == packet.get('account_ref')
            and installation.get('target_account_ref') == selected.get('account_ref')
            and binding.get('account_ref') == selected.get('account_ref')
            and binding.get('credential_profile_id') == selected.get('profile_id')
            and settings.get('active_profile_id') == selected.get('profile_id')
            and settings.get('monitor_enabled') is True)
        prepared = bool(matched and (context.get('credential_profile_id'), context.get('credential_revision'),
            context.get('canonical_account_ref'), context.get('binding_revision')) == (selected.get('profile_id'),
                selected.get('revision'), selected.get('account_ref'), binding.get('binding_revision')))
        prepared = (prepared and status.get('reconciliation_ready') is True
                    and management.get('settings_revision') == settings.get('revision'))
        self._management_prepare.setEnabled(self._worker is None and matched and not prepared)
        self._management_accept.setEnabled(self._worker is None and prepared
            and settings.get('mock_order_enabled') is True
            and management.get('status') in {'RECONCILIATION_ONLY', 'MANAGEMENT_READY'} and control.get('desired_state') == 'STOPPED'
            and type(control.get('control_revision')) is int)
        self._management_stop.setEnabled(self._worker is None and prepared
            and management.get('status') == 'MANAGEMENT_READY' and control.get('desired_state') == 'RUNNING')
        if packet:
            label = '설치 상태 미확인' if not installation else '자료 설치됨'
            if prepared:
                label = '대사 준비됨 · 관리 수락 필요'
                if not settings.get('mock_order_enabled'):
                    label = '대사 준비됨 · 모의주문 허용 후 다시 관리 준비가 필요합니다'
                if management.get('status') == 'MANAGEMENT_READY':
                    runner = (status.get('supervisor') or {}).get('runner') or {}
                    applied = (status.get('management_ready') is True
                        and (runner.get('spec_id'), runner.get('execution_run_id'))
                        == (context.get('spec_id'), context.get('execution_run_id')))
                    label = '현재 관리 수락됨 · ' + ('원주문 관리 연결됨' if applied else '실행 상태 미확인')
                    if control.get('desired_state') == 'STOPPED':
                        label = '현재 관리 중지됨 · 다시 관리하려면 현재 상태를 확인해 수락하세요'
                    elif runner.get('state') in {'ERROR', 'BLOCKED'}:
                        label = '현재 관리 보류 · 최신 계좌와 주문 상태를 확인하세요'
                        self._management_label.setToolTip(str(runner.get('reason') or ''))
            quantities = []
            if type(status.get('original_quantity')) is int:
                quantities.append('원매수 ' + str(status['original_quantity']) + '주')
            current_order = status.get('current_order') or {}
            for field, title in (('owned_quantity', '현재 보유'), ('working_quantity', '원주문 잔량')):
                if current_order:
                    quantity = current_order.get(field)
                    quantities.append(title + (' ' + str(quantity) + '주' if type(quantity) is int else ' 미확인'))
            prefix = str(packet.get('symbol', '')) + (' · ' + ' · '.join(quantities) if quantities else '')
            self._management_label.setText(prefix + ' · ' + label + ' · 신규 진입 꺼짐')

    def _spec_changed(self) -> None:
        if self._spec.signalsBlocked():
            return
        self._context["selected_spec_id"] = str(self._spec.currentData() or "")
        self._spec.setToolTip(self._context["selected_spec_id"])
        self._apply_button_state()

    def _selection_changed(self) -> None:
        if self._selection.signalsBlocked():
            return
        self._context["selected_account_selection_id"] = str(self._selection.currentData() or "")
        selection = selected_account_selection(self._context)
        if selection and selection.get("member_spec_ids"):
            self._context["selected_spec_id"] = selection["member_spec_ids"][0]
        self._apply_context()

    def _prepare_account_selection(self) -> None:
        if self._worker is not None:
            return
        selected = self._selected_profile()
        binding = self._context.get("candidate_binding")
        if not isinstance(selected, dict) or not isinstance(binding, dict):
            self._message.setText("모의계좌와 현재 계좌 확인 정보를 먼저 조회하세요.")
            return
        try:
            editor_values = self._replacement_editor_values()
        except (KeyError, TypeError, ValueError) as error:
            self._message.setText(str(error))
            return
        dialog = MockAutomationAccountSelectionDialog(
            self._context.get("specs", []), binding, selected["profile_id"],
            now_provider=self._content.server_now,
            selection=selected_account_selection(self._context), parent=self,
            **editor_values,
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            values = dialog._prepared_values or dialog.publication_values()
        except (KeyError, TypeError, ValueError) as error:
            self._message.setText("계좌 구성 저장 실패 · " + str(error))
            return
        finally:
            dialog.deleteLater()
        self._start_worker("publish_account_selection", values)

    def _replacement_editor_values(self):
        status = self._context.get('status') or {}
        control = status.get('control') or {}
        active = control.get('account_selection')
        runner = status.get('runner') or {}
        if not active:
            if control and status.get('runtime_mode') == 'AUTOMATIC':
                ref = next((row.get('admission_ref') for row in self._context.get('specs', ())
                    if isinstance(row, dict) and (row.get('spec') or {}).get('spec_id') == control.get('active_spec_id')), None)
                if (not isinstance(ref, dict) or ref.get('original_selection_id') is not None
                        or (ref.get('spec_id'), ref.get('execution_run_id'))
                        != (control.get('active_spec_id'), control.get('execution_run_id'))
                        or (runner.get('spec_id'), runner.get('execution_run_id'))
                        != (control.get('active_spec_id'), control.get('execution_run_id'))):
                    raise ValueError('현재 단일 운용의 원입장과 실행 상태를 새로 확인하세요.')
                return dict(original_anchor=control['active_spec_id'], admission_refs=[ref])
            return {}
        refs = runner.get('admission_refs')
        if (runner.get('account_selection_id') != active.get('selection_id') or not isinstance(refs, list)
                or {value.get('spec_id') for value in refs} != set(active.get('member_spec_ids', []))):
            raise ValueError('현재 구성의 원입장 정보를 새로 확인한 뒤 교체 구성을 작성하세요.')
        return dict(current_selection=active, admission_refs=refs)

    def _request(self, operation: str) -> None:
        if self._worker is not None:
            return
        selected = self._selected_profile()
        spec_id = str(self._spec.currentData() or self._context.get("selected_spec_id", ""))
        values: dict[str, Any] = {}
        if operation == 'refresh' and self._management_packet:
            values['management_operation_id'] = self._management_packet['operation_id']
        if selected is not None:
            values.update({
                "account_ref": str(selected["account_ref"]),
                "credential_profile_id": str(selected["profile_id"]),
                "spec_id": spec_id,
            })
            selection_id = self._context.get("selected_account_selection_id")
            if selection_id and operation in {"refresh", "start", 'replace_selection'}:
                values["account_selection_id"] = selection_id
        if operation != "refresh":
            if selected is None or not spec_id:
                self._message.setText("모의계좌와 READY 명세를 먼저 선택하세요.")
                return
            settings = self._context.get("account_settings", {})
            if operation in {"start", "resume", 'replace_selection'}:
                values["expected_settings_revision"] = int(settings.get("revision", -1))
                values["credential_revision"] = int(selected["revision"])
            if operation in {"stop", "resume", 'replace_selection'}:
                control = (self._context.get("status") or {}).get("control")
                if not isinstance(control, dict):
                    self._message.setText("현재 제어 revision을 다시 확인하세요.")
                    return
                values["expected_control_revision"] = int(control["control_revision"])
                reason = self._reason.text().strip()
                if not reason:
                    self._message.setText("중지·재개·교체 이유를 입력하세요.")
                    return
                values["reason"] = reason
        self._start_worker(operation, values)

    def _start_worker(self, operation: str, values: dict[str, Any]) -> None:
        self._set_busy(True)
        worker = MockAutomationWorker(
            self._content, self._credentials, operation, values, self,
        )
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(lambda source=worker: self._worker_finished(source))
        self._worker = worker
        worker.start()

    def _prepare_candidate_files(self) -> None:
        if self._worker is not None:
            return
        selected = self._selected_profile()
        binding = self._context.get("candidate_binding")
        if not isinstance(selected, dict) or not isinstance(binding, dict):
            self._message.setText("연결된 모의계좌를 먼저 새로 확인하세요.")
            return
        if self._candidate_files_dialog is not None:
            self._candidate_files_dialog.stop()
            self._candidate_files_dialog.deleteLater()
        dialog = MockAutomationCandidateDialog(self._content, selected, binding, self)
        self._candidate_files_dialog = dialog
        dialog.candidate_published.connect(lambda: self._request("refresh"))
        dialog.exec()

    def _publish_selected_candidate(self) -> None:
        if self._worker is not None:
            return
        selected = self._selected_profile()
        candidate = self._candidate.currentData()
        binding = self._context.get("candidate_binding")
        if not isinstance(selected, dict) or not isinstance(candidate, dict) or not isinstance(binding, dict):
            self._message.setText("게시 가능한 모의계좌 후보를 먼저 선택하세요.")
            return
        self._set_busy(True)
        worker = MockAutomationWorker(self._content, self._credentials, "prepare_publication", {
            "account_ref": str(selected["account_ref"]),
            "credential_profile_id": str(selected["profile_id"]),
            "spec_id": str(self._spec.currentData() or ""),
            "candidate_publication": candidate,
        }, self)
        request_token = object()
        self._context["_publication_request"] = request_token
        prepared: list[dict[str, Any]] = []
        worker.completed.connect(lambda _operation, context: prepared.append(context))
        worker.failed.connect(self._failed)

        def finished() -> None:
            current_request = self._worker is worker and self._context.get("_publication_request") is request_token
            if self._context.get("_publication_request") is request_token:
                self._context.pop("_publication_request")
            interrupted = worker.isInterruptionRequested()
            self._worker_finished(worker)
            if not current_request or not prepared or interrupted or not self.isVisible():
                return
            context = prepared[0]
            self._completed("prepare_publication", {
                key: value for key, value in context.items()
                if key not in {"publication_candidate", "shadow_events"}
            })
            self._publish_prepared_candidate(context)

        worker.finished.connect(finished)
        self._worker = worker
        worker.start()

    def _update_candidate_action(self) -> None:
        candidate = self._candidate.currentData()
        label = _minute_research_label(candidate)
        self._publish.setText(label + ' READY 명세 작성' if label else '새 READY 명세 작성')

    def _publish_prepared_candidate(self, context: dict[str, Any]) -> None:
        selected = context["selected_profile"]
        candidate = context["publication_candidate"]
        binding = context["candidate_binding"]
        for index in range(self._candidate.count()):
            if self._candidate.itemData(index) == candidate:
                self._candidate.setCurrentIndex(index)
                break
        events = matching_shadow_events(
            candidate,
            tuple(value for value in context.get("shadow_events", ()) if isinstance(value, dict)),
        )
        if not events:
            self._message.setText("이 후보 설정과 일치하는 실제 Shadow 후보 기록이 아직 없습니다.")
            return
        dialog = MockAutomationPublicationDialog(
            candidate, binding, str(selected["profile_id"]), events, self,
        )
        if (dialog.exec() != QDialog.DialogCode.Accepted
                or not self.isVisible() or self._worker is not None):
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
        if operation in {'selected_status', 'selected_prepare', 'selected_accept', 'selected_stop'}:
            self._management_status = context.get('selected_management')
            self._show_selected_management()
            self._message.setText('보관 주문의 최신 관리 상태를 확인했습니다.' if self._management_status
                else '요청 수락 후 최신 관리 상태를 확인하지 못했습니다. 파일을 다시 선택해 확인하세요.')
            return
        if 'selected_management' in context:
            self._management_status = context['selected_management']
        if "_publication_request" in context:
            context = {key: value for key, value in context.items() if key != "_publication_request"}
        messages = {
            "refresh": "NAS의 최신 모의 자동운용 상태를 확인했습니다.",
            "start": "모의 자동운용 시작 요청이 반영됐습니다.",
            "stop": "신규 자동 주문을 중지했습니다.",
            "resume": "모의 자동운용 재개 요청이 반영됐습니다.",
            "publish_spec": "READY 모의운용 명세를 게시했습니다. 주문은 아직 시작되지 않았습니다.",
            "publish_account_selection": "계좌 구성을 저장했습니다. 자동운용 시작 시 적용됩니다.",
            'replace_selection': '선택 구성을 적용했습니다. 기존 주문과 보유는 원래 명세로 관리하며 중지·재개 상태를 유지합니다.',
            "prepare_publication": "현재 선택한 후보의 게시 근거를 확인했습니다.",
        }
        if context.get("context_refresh_error"):
            self._context["status"] = context["status"]
            self._runtime.setText("상태를 확인할 수 없습니다. 새로 확인 후 제어할 수 있습니다.")
            self._apply_button_state()
            self._message.setText(
                f"{messages[operation]} 최신 상태를 확인하지 못했습니다. 새로 확인 후 제어하세요. "
                f"· {context['context_refresh_error']}"
            )
            return
        self._context = context
        self._apply_context()
        status = context.get("status")
        if isinstance(status, dict) and status.get("status_query_error"):
            acknowledged = messages[operation] + " " if operation in {"start", "stop", "resume", 'replace_selection', "publish_spec", "publish_account_selection"} else ""
            self._message.setText(acknowledged + "NAS 운용 상태를 확인하지 못했습니다. 새로 확인 후 제어하세요.")
        else:
            self._message.setText(messages.get(operation, "완료"))

    def _failed(self, _operation: str, message: str) -> None:
        if _operation in {'selected_status', 'selected_prepare', 'selected_accept', 'selected_stop'}:
            self._management_status = None
            self._show_selected_management()
            self._management_label.setText('관리 상태 미확인 · ' + message)
            return
        self._context["status"] = {"status_query_error": message or "상태 미확인"}
        self._runtime.setText("상태를 확인할 수 없습니다. 새로 확인 후 제어할 수 있습니다.")
        self._runtime.setToolTip("")
        self._apply_button_state()
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
        self._runtime.setToolTip("")
        self._members_status.clear()
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
            label = _minute_research_label(publication)
            label = label + ' · ' + status if label else status
            self._candidate.addItem(
                f"{package.get('strategy_ref', '')} · {label} · {format_kst_time(finished, missing='')}", publication,
            )

        selected_spec_id = str(self._context.get("selected_spec_id", ""))
        self._spec.blockSignals(True)
        self._spec.clear()
        for row in self._context.get("specs", ()):
            if not isinstance(row, dict) or not isinstance(row.get("spec"), dict):
                continue
            spec = row["spec"]
            label = f"{spec.get('strategy_ref', '')} · {row.get('readiness', 'BLOCKED')}"
            if spec.get("execution_plan_mode") == LEADER_NATIVE_PLAN_MODE:
                label += " · 계획형 0B 모의"
                if (spec.get('unfilled_execution_policy') or {}).get('version') == 'mock-unfilled-execution-policy/v2':
                    label += " · 현재 조건 재판단·정정 우선"
            elif spec.get('execution_plan_mode') == PANIC_NATIVE_PLAN_MODE:
                terms = spec.get('panic_execution_policy', {})
                management = ('원래 계획 취소·청산 유지' if terms.get('stopped_management') == 'continue_original_plan/v1'
                              else '대사만 유지')
                label += ' · 낙주 모의 · 현재 조건 재판단·정정 우선 · ' + management
            elif spec.get("execution_plan_mode") == CLOSE_NATIVE_PLAN_MODE:
                close_policy = spec.get("close_execution_policy", {})
                management = ("중지 후 원계획 관리" if close_policy.get("stopped_management") == "continue_original_plan/v1"
                              else "중지 후 대사만 유지")
                candidate = next((item for item in self._context.get('candidate_publications', ())
                    if isinstance(item, dict) and isinstance(item.get('package'), dict)
                    and item['package'].get('package_hash') == spec.get('candidate_package_hash')), None)
                label += " · " + (_minute_research_label(candidate) or '종가베팅') + " 모의 · " + management
                if close_policy.get('accepted_order_management') == 'reassess_then_amend_price/v1':
                    label += " · 현재 조건 재판단·정정 우선"
            self._spec.addItem(label, spec.get("spec_id", ""))
        index = self._spec.findData(selected_spec_id)
        self._spec.setCurrentIndex(index if index >= 0 else 0)
        self._spec.blockSignals(False)
        self._context["selected_spec_id"] = str(self._spec.currentData() or "")
        self._spec.setToolTip(self._context["selected_spec_id"])

        selected_selection_id = str(self._context.get("selected_account_selection_id", ""))
        self._selection.blockSignals(True)
        self._selection.clear()
        self._selection.addItem("단일 명세 운용", "")
        for saved in self._context.get("account_selections", ()):
            if not isinstance(saved, dict):
                continue
            member_ids = saved.get("member_spec_ids", [])
            names = [self._spec_name(identity) for identity in member_ids]
            self._selection.addItem(" + ".join(names) + " · " + format_kst_time(saved.get("frozen_at"), missing=""),
                                    saved.get("selection_id", ""))
        index = self._selection.findData(selected_selection_id)
        if selected_selection_id and index < 0:
            self._selection.addItem("적용 구성 · 저장 목록 재확인 필요", selected_selection_id)
            index = self._selection.count() - 1
        self._selection.setCurrentIndex(max(0, index))
        self._selection.blockSignals(False)
        self._show_account_selection()

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
        account_reasons = account_preparation_reasons(self._context)
        if account_reasons:
            self._readiness.setText(self._readiness.text() + " · " + " ".join(account_reasons))
        status = self._context.get("status")
        if isinstance(status, dict) and status.get("status_query_error"):
            self._runtime.setText("상태 조회 실패 · " + str(status["status_query_error"]))
        elif isinstance(status, dict):
            control = status.get("control")
            desired = control.get("desired_state") if isinstance(control, dict) else "미시작"
            if isinstance(control, dict) and control.get("entry_resume_required", False):
                desired = str(desired) + " · 신규 진입 재개 대기"
            runner = status.get("runner")
            runner_state = ("계좌 공통 실행" if isinstance(runner, dict)
                            and runner.get("version") == "mock_automation_account_runner_status/v1" else
                            runner.get("state") if isinstance(runner, dict) else "없음")
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
                        f" ({format_kst_time(processed_at, '%H:%M:%S')} KST)"
                    )
                if latency.get("future_or_invalid_bar_time_count"):
                    latency_text += " · 분봉 시각 확인 필요"
            protection_text = ""
            protection = runner.get("planned_protection") if isinstance(runner, dict) else None
            if isinstance(protection, dict):
                state = protection["state"] if isinstance(protection.get("state"), str) else ""
                label = {
                    "WAIT_NATIVE_READBACK": "원장 확인 대기",
                    "PREPARED_NO_INTENT": "보호 준비",
                    "SUBMISSION_UNKNOWN": "보호 매도 접수 미확인",
                    "ACCEPTED": "보호 매도 접수",
                    "PARTIALLY_FILLED": "보호 매도 일부 체결",
                    "FILLED": "보호 매도 체결 완료",
                }.get(state, f"보호 매도 {state}" if state else "보호 매도 상태 미확인")
                quantities = " · ".join(f"{text} {value}주" for field, text in (
                    ("plan_quantity", "계획 보유"), ("reserved_quantity", "주문 예약"),
                    ("detailed_filled_quantity", "상세 체결"),
                    ("unreconciled_sell_quantity", "체결 대사 대기"),
                ) if type(value := protection.get(field)) is int and value >= 0)
                protection_text = " · " + " · ".join(value for value in (label, quantities) if value)
                self._runtime.setToolTip("\n".join(f"{text} {value}" for field, text in (
                    ("plan_id", "계획 ID"), ("plan_revision", "계획 판본"), ("intent_id", "보호 주문 의도 ID"),
                ) if (value := protection.get(field)) is not None))
            self._runtime.setText(
                f"모드 {status.get('runtime_mode') or '수동'} · 제어 {desired} · "
                + ("실행 상태 " if runner_state == "계좌 공통 실행" else "runner ") + str(runner_state)
                + (f" · 복원 오류 {error}" if error else "")
                + latency_text
                + protection_text
            )
        else:
            self._runtime.setText("상태를 확인할 수 없습니다.")
        self._apply_button_state()

    def _spec_name(self, identity: str) -> str:
        return next((str(row["spec"].get("strategy_ref") or identity)
                     for row in self._context.get("specs", ()) if isinstance(row, dict)
                     and isinstance(row.get("spec"), dict) and row["spec"].get("spec_id") == identity), identity)

    def _show_account_selection(self) -> None:
        self._selection_details.setToolTip("")
        selection = selected_account_selection(self._context)
        status = self._context.get("status") or {}
        control = status.get("control") or {}
        active = control.get("account_selection") or {}
        runner = status.get("runner") or {}
        active_id = active.get("selection_id")
        runner_id = runner.get("account_selection_id")
        lines = []
        if selection:
            identity = selection.get("selection_id")
            if status.get("status_query_error") or (active_id and runner_id and active_id != runner_id):
                lines.append("저장됨 · 적용 상태 미확인")
            elif identity == active_id:
                lines.append("저장됨 · 현재 제어에 적용됨")
            else:
                lines.append("저장됨 · 시작 전 구성")
            members = selection.get("member_spec_ids", [])
            if members:
                lines.append("계좌 기준 · " + self._spec_name(members[0]))
            order = selection.get("execution_order_spec_ids", [])
            lines.append("실행 순서 · " + (" → ".join(self._spec_name(value) for value in order)
                                          if order else "미지정 · 수정 후 저장하세요"))
            limits = selection.get("limits", {})
            if selection.get('version') == 'mock_automation_account_selection/v2':
                lines.append('신규 진입 · ' + (' · '.join(self._spec_name(value) for value in selection.get('entry_spec_ids', [])) or '모두 중지'))
                lines.append('기존 관리 유지 · ' + ' · '.join(self._spec_name(value['spec_id'])
                    for value in selection.get('retained_admission_refs', [])))
            lines.append("공통 한도 · " + " · ".join(f"{label} {limits[name]:,}" for name, label, *_ in _LIMIT_INPUTS
                                                      if type(limits.get(name)) is int))
            self._selection_details.setToolTip(str(identity))
        elif self._context.get("selected_account_selection_id"):
            lines.append("적용 구성의 저장 내용을 확인할 수 없습니다. 새로 확인하세요.")
        else:
            lines.append("선택한 단일 명세의 기존 한도를 사용합니다.")
        self._selection_details.setText("\n".join(lines))
        if runner.get("version") == "mock_automation_account_runner_status/v1":
            member_lines = []
            for member in runner.get("members", ()):
                if not isinstance(member, dict):
                    continue
                state = str(member.get("state") or "")
                state_label = {"READY": "준비", "STOPPED": "중지", "BLOCKED": "실행 차단", "ERROR": "오류",
                               "WARMUP": "입력 복구 중", "RUNNING": "운용 중"}.get(state, state or "상태 미확인")
                text = self._spec_name(str(member.get("spec_id", ""))) + " · " + state_label
                protection = member.get("planned_protection")
                if isinstance(protection, dict):
                    protection_state = str(protection.get("state") or "")
                    text += " · " + {"WAIT_NATIVE_READBACK": "원장 확인 대기", "PREPARED_NO_INTENT": "보호 준비",
                        "SUBMISSION_UNKNOWN": "보호 매도 접수 미확인", "ACCEPTED": "보호 매도 접수",
                        "PARTIALLY_FILLED": "보호 매도 일부 체결", "FILLED": "보호 매도 체결 완료"}.get(
                            protection_state, "보호 매도 " + (protection_state or "상태 미확인"))
                    for name, label in (("plan_quantity", "계획 보유"), ("reserved_quantity", "주문 예약"),
                                        ("detailed_filled_quantity", "상세 체결"),
                                        ("unreconciled_sell_quantity", "체결 대사 대기")):
                        if type(protection.get(name)) is int:
                            text += f" · {label} {protection[name]}주"
                member_lines.append(text)
            self._members_status.setText("\n".join(member_lines) or "명세별 상태 미확인")

    def _apply_button_state(self) -> None:
        self._show_selected_management()
        state = operation_availability(self._context)
        self._spec.setEnabled(self._worker is None and not self._context.get("selected_account_selection_id"))
        self._start.setEnabled(state["start"] and self._worker is None)
        self._stop.setEnabled(state["stop"] and self._worker is None)
        self._resume.setEnabled(state["resume"] and self._worker is None)
        self._replace_selection.setEnabled(replacement_available(self._context) and self._worker is None)

    def _set_busy(self, busy: bool) -> None:
        self._management_file.setEnabled(not busy)
        if busy:
            self._management_prepare.setEnabled(False)
            self._management_accept.setEnabled(False)
            self._management_stop.setEnabled(False)
        self._refresh.setEnabled(not busy)
        self._profile.setEnabled(not busy)
        self._spec.setEnabled(not busy and not self._context.get("selected_account_selection_id"))
        self._selection.setEnabled(not busy)
        self._edit_selection.setEnabled(not busy and isinstance(self._context.get("candidate_binding"), dict))
        self._candidate.setEnabled(not busy)
        self._candidate_files.setEnabled(
            not busy and isinstance(self._context.get("selected_profile"), dict)
            and isinstance(self._context.get("candidate_binding"), dict)
        )
        self._publish.setEnabled(not busy and self._candidate.count() > 0)
        if busy:
            self._start.setEnabled(False)
            self._stop.setEnabled(False)
            self._resume.setEnabled(False)
            self._replace_selection.setEnabled(False)
        else:
            self._apply_button_state()

    def stop(self) -> None:
        self._window_settings.setValue("geometry", self.saveGeometry())
        self._context.pop("_publication_request", None)
        if self._candidate_files_dialog is not None:
            self._candidate_files_dialog.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(12_000)

    def closeEvent(self, event) -> None:
        self._window_settings.setValue("geometry", self.saveGeometry())
        self._context.pop("_publication_request", None)
        event.ignore()
        self.hide()
