from __future__ import annotations

from kiwoom_monitor.domain.time_basis import as_kst, kst_wall_now, kst_now

import threading
import sqlite3
import math
import json
from dataclasses import asdict
from copy import deepcopy
from pathlib import Path
from datetime import datetime
from typing import Any

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QTabWidget, QWidget, QComboBox, QLineEdit, QFileDialog, QScrollArea, QPlainTextEdit,
)

from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.application.leader_breakout import LeaderBreakoutPolicy, document_leader_breakout_policy, document_baseline_missing
from kiwoom_monitor.application.research_families import (
    BREAKOUT_FAMILY_ID, RESEARCH_DIRECTION_NOTICE, RESEARCH_FAMILY_LABELS,
)
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_operational_settings import CentralOperationalSettingsClient


class CandidatePollWorker(QThread):
    pageReceived = Signal(dict)
    failed = Signal(str)

    def __init__(self, client: CentralContentClient, parent=None, *, poll_seconds: float = 2.0) -> None:
        super().__init__(parent)
        self._client = client
        self._poll_milliseconds = max(500, int(poll_seconds * 1000))
        self._cursor = 0
        self._initial = True
        self._condition = threading.Condition()
        self._polling_enabled = False
        self._suppress_alerts_until_caught_up = False

    def set_polling_enabled(self, enabled: bool) -> None:
        with self._condition:
            enabled = bool(enabled)
            if enabled and not self._polling_enabled:
                # Catch up silently after a period with no UI or alert consumer.
                self._suppress_alerts_until_caught_up = True
            self._polling_enabled = enabled
            self._condition.notify_all()

    def wake_for_shutdown(self) -> None:
        with self._condition:
            self._polling_enabled = True
            self._condition.notify_all()

    def run(self) -> None:
        while not self.isInterruptionRequested():
            with self._condition:
                while not self._polling_enabled and not self.isInterruptionRequested():
                    self._condition.wait()
                if self.isInterruptionRequested():
                    return
            try:
                page = self._client.load_candidate_events(
                    after_sequence=self._cursor,
                    limit=1000,
                )
                next_cursor = page.get("next_cursor")
                if next_cursor is not None:
                    self._cursor = max(self._cursor, int(next_cursor))
                caught_up = not bool(page.get("has_more"))
                with self._condition:
                    page["initial_sync"] = (
                        self._initial or self._suppress_alerts_until_caught_up
                    )
                    if caught_up:
                        self._initial = False
                        self._suppress_alerts_until_caught_up = False
                if caught_up:
                    self._cursor = max(self._cursor, int(page.get("high_watermark", self._cursor)))
                page["consumed_sequence"] = self._cursor
                self.pageReceived.emit(page)
                # The ledger is immutable, but the server projects expiry and
                # current episode validity. Incremental delivery alone cannot
                # refresh rows already shown. Keep the forward cursor separate.
                with self._condition:
                    refresh = caught_up and self._cursor > 0 and self._polling_enabled
                if refresh and not self.isInterruptionRequested():
                    try:
                        current = self._client.load_candidate_events(
                            after_sequence=max(0, self._cursor - 1000), limit=1000)
                        self.pageReceived.emit({**current, "status_only": True})
                    except (RuntimeError, ValueError, OSError) as error:
                        self.pageReceived.emit({"events": [], "status_only": True, "status_error": str(error)})
            except (RuntimeError, ValueError, OSError) as error:
                self.failed.emit(str(error))
            with self._condition:
                if self._polling_enabled and not self.isInterruptionRequested():
                    self._condition.wait(self._poll_milliseconds / 1000)


class CandidateSettingsWorker(QThread):
    settingsReady = Signal(dict)
    failed = Signal(str)

    def __init__(
        self, client: CentralOperationalSettingsClient, changes: dict[str, object] | None = None,
        parent=None, *, request=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._changes = changes
        self._request = request

    def run(self) -> None:
        try:
            values = self._request() if self._request is not None else (
                self._client.load() if self._changes is None else self._client.save(self._changes))
            self.settingsReady.emit(values)
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
            self.failed.emit(str(error))


def _prepare_position_outcome_document(document, database, selection_path, role):
    """Explicit PC read in the existing worker; retain every supplied target/policy."""
    from kiwoom_monitor.application.position_target import (
        position_action_comparison_inputs_from_document, evaluate_position_action_comparison_shadow)
    from kiwoom_monitor.application.research_evaluation import read_position_action_outcomes
    from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
    def checkpoint():
        if QThread.currentThread().isInterruptionRequested():
            raise RuntimeError("완결 자료 읽기가 중단되었습니다.")
    checkpoint()
    try:
        if not database or not selection_path or role not in {'TRAIN', 'VALIDATION'}:
            raise ValueError("연구 DB·자료 선택 JSON·학습/검증 구간을 지정하세요.")
        if not isinstance(document, dict) or document.get('outcomes') != []:
            raise ValueError("비교 입력을 가져온 뒤 기존 행동 자료를 먼저 해제하세요.")
        inputs = position_action_comparison_inputs_from_document(document)
        evaluate_position_action_comparison_shadow(*inputs)
        parameters, requests = inputs[:2]
        first = requests[0]
        with Path(selection_path).open('rb') as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("자료 선택 JSON은 256 KiB 이하여야 합니다.")
        checkpoint()
        outcomes, receipt = read_position_action_outcomes(
            ResearchRepository(Path(database), read_only=True).load_position_action_evidence,
            json.loads(raw.decode('utf-8-sig')), decision_time=first.decision_time,
            horizon_seconds=parameters.horizon_seconds, population_ref=parameters.population_ref,
            environment=first.environment, symbol=first.symbol, development_role=role, checkpoint=checkpoint)
        prepared = {**deepcopy(document), 'outcomes': json.loads(json.dumps([asdict(o) for o in outcomes],
            default=lambda value: value.isoformat()))}
        if len(json.dumps(prepared, ensure_ascii=False).encode('utf-8')) > 256 * 1024:
            raise ValueError("완결 자료를 포함한 비교 입력은 256 KiB 이하여야 합니다.")
        checkpoint()
        result = evaluate_position_action_comparison_shadow(*position_action_comparison_inputs_from_document(prepared))
        checkpoint()
        return {'document': prepared, 'receipt': receipt,
                'ev_available_count': sum(row['ev_status']=='EMPIRICAL_ESTIMATE' for row in result['actions'])}
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("완결 자료의 입력 형식을 확인할 수 없습니다. " + str(error)) from error


class CandidateMonitorDialog(QDialog):
    """PC-local consumer; NAS generation continues independently of this window."""

    def __init__(
        self, client: CentralContentClient,
        operational_client: CentralOperationalSettingsClient | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shadow 후보")
        self.resize(820, 850)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._settings = QSettings("KiwoomMonitor", "CandidateMonitor")
        self._known_events: set[str] = set()
        self._event_rows: dict[str, int] = {}
        self._operational_client = operational_client
        self._settings_worker: CandidateSettingsWorker | None = None
        self._operational_settings_loaded = False
        self._strategy_config = document_leader_breakout_policy().to_dict()
        self._legacy_candidate_settings = None
        self._leader_policy_text = json.dumps(self._strategy_config, ensure_ascii=False, indent=2)
        self._content_client = client
        self._ram_selection_loaded = False
        self._ram_supported = False
        self._ram_stopping = False
        self._ram_page = None
        self._ram_recorded = None
        self._ram_saved_stock_thesis = None
        self._ram_position_document = None
        self._ram_position_generation = 0
        self._ram_position_read_pending = False

        self._quality = QLabel("NAS 후보 상태를 확인하는 중입니다…")
        self._quality.setWordWrap(True)
        self._alert_enabled = QCheckBox("새 후보 화면·소리 알림")
        self._alert_enabled.setChecked(
            str(self._settings.value("alert_enabled", "0")) == "1"
        )
        self._alert_enabled.toggled.connect(self._on_alert_enabled_changed)
        top = QHBoxLayout()
        top.addWidget(self._quality, 1)
        top.addWidget(self._alert_enabled)

        self._candidate_enabled = QCheckBox("명시 정책으로 0B 돌파 후보 관찰")
        self._strategy_name = QLabel("주도주 구조 돌파 · 0B · 주문 승인 없음")
        self._strategy_selector = QComboBox()
        self._strategy_selector.addItem("주도주 구조 돌파 · 실시간 0B", "leader_breakout/v1")
        self._strategy_selector.addItem("기존 완료봉 돌파 · 저장된 비교 설정", "legacy_comparison")
        self._strategy_selector.model().item(1).setEnabled(False)
        self._server_policy_status = QLabel("서버 적용 정책 확인 전 · 화면 선택만으로 서버 설정이 바뀌지 않습니다.")
        self._server_policy_status.setWordWrap(True)
        self._lookback_bars = QSpinBox(); self._lookback_bars.setRange(1, 240)
        self._buffer_percent = QDoubleSpinBox(); self._buffer_percent.setRange(0, 30); self._buffer_percent.setDecimals(2); self._buffer_percent.setSuffix(" %")
        self._rank_persistence = QCheckBox("순위 지속 조건 사용")
        self._stop_loss_percent = QDoubleSpinBox(); self._stop_loss_percent.setRange(0.01, 30); self._stop_loss_percent.setDecimals(2); self._stop_loss_percent.setSuffix(" %")
        self._target_percent = QDoubleSpinBox(); self._target_percent.setRange(0.01, 100); self._target_percent.setDecimals(2); self._target_percent.setSuffix(" %")
        self._max_hold_minutes = QSpinBox(); self._max_hold_minutes.setRange(1, 1440); self._max_hold_minutes.setSuffix(" 분")
        self._universe_max_age = QSpinBox(); self._universe_max_age.setRange(1, 3600); self._universe_max_age.setSuffix(" 초")
        self._settings_status = QLabel("NAS 설정을 불러오는 중입니다…")
        self._settings_status.setWordWrap(True)
        self._save_settings = QPushButton("Shadow 설정 저장")
        self._save_settings.clicked.connect(self._save_operational_settings)
        self._settings_group = QGroupBox("주도주 구조 돌파")
        settings_form = QFormLayout(self._settings_group)
        settings_form.addRow("판단 정책", self._strategy_selector)
        settings_form.addRow(self._server_policy_status)
        settings_form.addRow("작동", self._candidate_enabled)
        settings_form.addRow("전략", self._strategy_name)
        settings_form.addRow("돌파 기준 봉", self._lookback_bars)
        settings_form.addRow("돌파 여유", self._buffer_percent)
        settings_form.addRow("관심순위", self._rank_persistence)
        settings_form.addRow("손절 기준", self._stop_loss_percent)
        settings_form.addRow("목표 기준", self._target_percent)
        settings_form.addRow("최대 관찰", self._max_hold_minutes)
        settings_form.addRow("순위 자료 유효시간", self._universe_max_age)
        self._settings_form = settings_form
        self._leader_policy_editor = QPlainTextEdit()
        self._leader_policy_editor.setPlainText(self._leader_policy_text)
        self._leader_policy_editor.setMaximumHeight(200)
        settings_form.addRow("주도주 돌파 명시 정책", self._leader_policy_editor)
        self._use_leader_policy = QPushButton("미정 초안으로 초기화")
        self._use_leader_policy.clicked.connect(self._reset_leader_policy)
        self._strategy_selector.currentIndexChanged.connect(self._select_candidate_policy)
        settings_form.addRow(self._use_leader_policy)
        settings_form.addRow(self._settings_status)
        settings_form.addRow(self._save_settings)
        self._settings_group.setEnabled(False)
        self._render_candidate_settings({"shadow_candidate_config": self._strategy_config})
        self._settings_group.setEnabled(False)
        self._settings_status.setText("NAS 설정을 불러오는 중입니다…")

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(("감지 시각", "종목", "기준가", "수량", "만료", "상태", "판단 정책"))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._baseline_status = QLabel('00종합 기준선 관측 대기 · 거래 조건의 미정 값은 유지합니다.')
        self._baseline_status.setWordWrap(True)
        self._baseline_table = QTableWidget(0,5)
        self._baseline_table.setHorizontalHeaderLabels(('종목','0B 관측가','직전 구간 고가','기준선 관측','거래 준비'))
        self._baseline_table.setMaximumHeight(160)
        self._baseline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)

        candidate_page = QWidget()
        candidate_layout = QVBoxLayout(candidate_page)
        candidate_layout.addLayout(top)
        candidate_layout.addWidget(self._settings_group)
        candidate_layout.addWidget(self._baseline_status)
        candidate_layout.addWidget(self._baseline_table)
        candidate_layout.addWidget(self._table)
        self._tabs = QTabWidget()
        self._tabs.addTab(candidate_page, "주도주 돌파 · 0B")
        self._tabs.addTab(self._build_ram_page(), "시장·테마 관찰")
        self._tabs.currentChanged.connect(self._tab_changed)
        layout = QVBoxLayout(self)
        self._research_direction = QLabel(RESEARCH_DIRECTION_NOTICE)
        self._research_direction.setWordWrap(True)
        layout.addWidget(self._research_direction)
        layout.addWidget(self._tabs)
        self._ram_timer = QTimer(self)
        self._ram_timer.setInterval(5000)
        self._ram_timer.timeout.connect(self._refresh_ram_if_visible)
        self._ram_timer.start()
        geometry = self._settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        self._worker = CandidatePollWorker(client, self)
        self._worker.pageReceived.connect(self._apply_page)
        self._worker.failed.connect(self._show_failure)
        self._sync_polling_state()
        if self._operational_client is None:
            self._settings_status.setText("NAS 운영 설정 연결을 사용할 수 없습니다.")

    def _now(self) -> datetime:
        provider = getattr(self._content_client, "server_now", None)
        current = provider() if callable(provider) else None
        return as_kst(current) if isinstance(current, datetime) else kst_now()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_polling_state()
        if not self._operational_settings_loaded and self._operational_client is not None:
            self._start_settings_request()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._sync_polling_state()

    def _on_alert_enabled_changed(self, checked: bool) -> None:
        self._settings.setValue("alert_enabled", "1" if checked else "0")
        self._sync_polling_state()

    def _sync_polling_state(self) -> None:
        if not hasattr(self, "_worker"):
            return
        enabled = (self.isVisible() and self._tabs.currentIndex() == 0) or self._alert_enabled.isChecked()
        self._worker.set_polling_enabled(enabled)
        if enabled and not self._worker.isRunning():
            self._worker.start()

    def _apply_page(self, page: dict[str, Any]) -> None:
        if page.get("status_only"):
            refreshed = set()
            for event in page.get("events", []):
                if not isinstance(event, dict):
                    continue
                event_id = str(event.get("event_id", ""))
                row = self._event_rows.get(event_id)
                if row is not None:
                    self._write_event(row, event)
                    refreshed.add(event_id)
            for event_id, row in self._event_rows.items():
                item = self._table.item(row, 5)
                if event_id not in refreshed and item.text() in {"ACTIVE", "현재 미확인"}:
                    item.setText("현재 미확인")
                    item.setToolTip(str(page.get("status_error") or "최근 1,000건 상태 확인 범위 밖입니다."))
            return
        quality = page.get("quality", {})
        self._render_document_baselines(quality)
        status = _quality_status(quality.get("status", "UNKNOWN"))
        reason = _quality_reason(quality.get("reason", ""))
        self._quality.setText(f"NAS 후보: {status}" + (f" · {reason}" if reason else ""))
        self._quality.setToolTip(str(quality.get('reason','')))
        initial = bool(page.get("initial_sync"))
        self._settings.setValue(
            "last_consumed_sequence", str(max(0, int(page.get(
                "consumed_sequence", page.get("high_watermark", 0),
            )))),
        )
        new_active = 0
        for event in page.get("events", []):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id", ""))
            if not event_id:
                continue
            if event_id in self._known_events:
                row = self._event_rows.get(event_id)
                if row is not None:
                    self._write_event(row, event)
                continue
            self._known_events.add(event_id)
            self._append_event(event)
            if not initial and event.get("status") == "ACTIVE":
                new_active += 1
        if new_active and self._alert_enabled.isChecked():
            try:
                QApplication.beep()
            except Exception:
                pass
            self.show()
            self.raise_()
            self.activateWindow()

    def _render_document_baselines(self,quality):
        observations = quality.get('document_baseline_observations',())
        self._baseline_table.setRowCount(0)
        if not isinstance(observations,(tuple,list)) or len(observations)>200:
            self._baseline_status.setText('00종합 기준선 자료 확인 필요')
            return
        labels={'WATCH':'기준선 관찰','ABOVE_REFERENCE':'기준선 위 관측',
            'CROSSED_UNCONFIRMED':'가격 교차 관측 · 확인 미완료','DATA_BLOCKED':'자료/설정 미확인'}
        for value in observations:
            row=self._baseline_table.rowCount();self._baseline_table.insertRow(row)
            reference=value.get('reference',{})
            texts=(value.get('symbol',''),value.get('price'),reference.get('reference_price'),
                labels.get(value.get('state'),'미확인'),'거래 미승인 · 조건 미정')
            details=('00종합 ② 10절 · 현재 구간을 제외한 완료 분봉 고가 / 현재 가격은 0B',
                '주도주 선정·확인 조건·대금 문턱·무효화/위험 조건은 임의로 채우지 않습니다.',
                str(value.get('observed_at','')),
                str(reference.get('window_start',''))+' ~ '+str(reference.get('window_end','')),
                *(_leader_missing_label(name) for name in value.get('configuration_missing',())),*value.get('reasons',()),
                *reference.get('input_refs',()))
            for column,text in enumerate(texts):
                item=QTableWidgetItem('미정' if text is None else str(text));item.setToolTip('\n'.join(details))
                self._baseline_table.setItem(row,column,item)
        self._baseline_table.resizeColumnsToContents()
        self._baseline_status.setText('00종합 가격 기준선 관측 · '+str(len(observations))+'종목 · 주도주/진입 확정·주문 승인 아님')

    def _append_event(self, event: dict[str, Any]) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        if event.get("event_id"):
            self._event_rows[str(event["event_id"])] = row
        self._write_event(row, event)
        self._table.scrollToBottom()

    def _write_event(self, row: int, event: dict[str, Any]) -> None:
        values = (
            _display_time(event.get("available_at")),
            str(event.get("symbol", "")),
            f"{int(event.get('signal_reference_price', 0)):,}",
            "미정" if event.get("quantity") is None else str(event["quantity"]),
            _display_time(event.get("expires_at")),
            str(event.get("status", "")),
            ("주도주 구조 돌파 · 0B" if event.get("strategy_id") == "leader_breakout"
             else "기존 비교 · " + str(event.get("strategy_id", "출처 미확인"))),
        )
        for column, value in enumerate(values):
            self._table.setItem(row, column, QTableWidgetItem(value))

    def _show_failure(self, message: str) -> None:
        self._quality.setText(f"NAS 후보 연결 대기 · {message}")

    def _start_settings_request(self, changes: dict[str, object] | None = None) -> None:
        if self._operational_client is None or (
            self._settings_worker is not None and self._settings_worker.isRunning()
        ):
            return
        self._settings_group.setEnabled(False)
        self._settings_status.setText(
            "NAS에 저장하는 중입니다…" if changes is not None else "NAS 설정을 불러오는 중입니다…"
        )
        worker = CandidateSettingsWorker(self._operational_client, changes, self)
        worker.settingsReady.connect(self._apply_operational_settings)
        worker.failed.connect(self._show_settings_failure)
        worker.finished.connect(lambda source=worker: self._clear_settings_worker(source))
        self._settings_worker = worker
        worker.start()

    def _apply_operational_settings(self, values: dict[str, Any]) -> None:
        self._operational_settings_loaded = True
        self._operational_revision = values.get("revision")
        config = values.get("shadow_candidate_config")
        leader_mode = isinstance(config, dict) and "policy_id" in config
        implicit_draft = leader_mode and json.dumps(config,sort_keys=True) == json.dumps(LeaderBreakoutPolicy().to_dict(),sort_keys=True)
        self._legacy_candidate_settings = deepcopy(values) if isinstance(config, dict) and not leader_mode else None
        self._strategy_selector.model().item(1).setEnabled(self._legacy_candidate_settings is not None)
        server_policy = "주도주 구조 돌파 · 0B" if leader_mode else "기존 완료봉 비교" if isinstance(config, dict) else "미확인"
        self._server_policy_status.setText(
            f"서버 저장 #{self._operational_revision}: {server_policy} · "
            + ("켜짐" if values.get("shadow_candidate_enabled") else "꺼짐")
            + " · 편집한 정책은 저장 후 서버에 적용됩니다.")
        if leader_mode:
            self._leader_policy_text = json.dumps(document_leader_breakout_policy().to_dict() if implicit_draft else config,
                ensure_ascii=False,indent=2)
        selected_legacy = self._strategy_selector.currentData() == "legacy_comparison" and self._legacy_candidate_settings is not None
        blocked = self._strategy_selector.blockSignals(True)
        self._strategy_selector.setCurrentIndex(1 if selected_legacy else 0)
        self._strategy_selector.blockSignals(blocked)
        self._render_candidate_settings({**values,'shadow_candidate_enabled':False,
            'shadow_candidate_config':document_leader_breakout_policy().to_dict()} if implicit_draft else values if leader_mode or selected_legacy else {
            "shadow_candidate_enabled": False, "shadow_candidate_config": document_leader_breakout_policy().to_dict()})
        if not leader_mode and not selected_legacy:
            self._settings_status.setText("새 0B 정책 편집 중 · 서버의 기존 비교 설정은 아직 전환되지 않았습니다. "
                + self._settings_status.text())
        elif implicit_draft:
            self._settings_status.setText('00종합 초안 편집 중 · 서버의 기존 미정 설정은 저장 후 전환됩니다. '+self._settings_status.text())

    def _select_candidate_policy(self, _index: int) -> None:
        if "policy_id" in self._strategy_config:
            self._leader_policy_text = self._leader_policy_editor.toPlainText()
        if self._strategy_selector.currentData() == "legacy_comparison":
            if self._legacy_candidate_settings is not None:
                self._render_candidate_settings(self._legacy_candidate_settings)
        else:
            self._render_candidate_settings({"shadow_candidate_enabled": False,
                "shadow_candidate_config": document_leader_breakout_policy().to_dict()})

    def _reset_leader_policy(self) -> None:
        self._leader_policy_text = json.dumps(document_leader_breakout_policy().to_dict(), ensure_ascii=False, indent=2)
        if self._strategy_selector.currentIndex() == 0:
            self._render_candidate_settings({"shadow_candidate_config": document_leader_breakout_policy().to_dict()})
        else:
            self._strategy_selector.setCurrentIndex(0)

    def _render_candidate_settings(self, values: dict[str, Any]) -> None:
        config = values.get("shadow_candidate_config")
        leader_mode = isinstance(config, dict) and "policy_id" in config
        self._leader_policy_editor.setVisible(leader_mode)
        self._use_leader_policy.setVisible(leader_mode)
        self._settings_form.labelForField(self._leader_policy_editor).setVisible(leader_mode)
        for widget in (self._lookback_bars, self._buffer_percent, self._rank_persistence,
                self._stop_loss_percent, self._target_percent, self._max_hold_minutes, self._universe_max_age):
            widget.setVisible(not leader_mode)
            self._settings_form.labelForField(widget).setVisible(not leader_mode)
        if leader_mode:
            self._strategy_config = deepcopy(config)
            self._leader_policy_editor.setPlainText(self._leader_policy_text)
            self._candidate_enabled.setText("명시 정책으로 0B 돌파 후보 관찰")
            self._candidate_enabled.setChecked(bool(values.get("shadow_candidate_enabled", False)))
            self._strategy_name.setText("주도주 구조 돌파 · 0B · 주문 승인 없음")
            self._settings_group.setTitle("주도주 구조 돌파")
            try:
                policy = LeaderBreakoutPolicy.from_dict(json.loads(self._leader_policy_text))
                missing = policy.missing("0B")
                self._settings_status.setText("진입 차단 · 미정: " + ", ".join(missing) if missing else
                    "실제 0B 가격 교차 판단 · 명시 주도주/구조 근거 필요 · 주문 승인 없음")
                if policy.reference_level is not None:
                    missing = document_baseline_missing(policy,'0B')
                    self._candidate_enabled.setText('00종합 기준선의 0B 관측·기록')
                    self._strategy_name.setText('주도주 구조 돌파 · 0B · 00종합 기준선 관측')
                    self._settings_status.setText('00종합 기준선 관측 · 현재 구간 제외 · 주문 승인 없음 · 미정: '+
                        ', '.join(_leader_missing_label(name) for name in missing))
            except (ValueError, TypeError) as error:
                self._settings_status.setText("정책 확인 필요 · " + str(error))
            self._settings_group.setEnabled(True)
            return
        self._candidate_enabled.setText("완료봉 비교 실험의 후보 기록 사용")
        self._strategy_name.setText(RESEARCH_FAMILY_LABELS[BREAKOUT_FAMILY_ID])
        self._settings_group.setTitle("기존 완료봉 비교 실험 설정")
        if isinstance(config, dict):
            self._strategy_config = {**default_shadow_breakout_config().to_dict(), **config}
        self._candidate_enabled.setChecked(bool(values.get("shadow_candidate_enabled", False)))
        self._lookback_bars.setValue(int(self._strategy_config["lookback_bars"]))
        self._buffer_percent.setValue(float(self._strategy_config["buffer_bps"]) / 100)
        self._rank_persistence.setChecked(bool(self._strategy_config["rank_persistence_enabled"]))
        self._stop_loss_percent.setValue(float(self._strategy_config["stop_loss_bps"]) / 100)
        self._target_percent.setValue(float(self._strategy_config["target_bps"]) / 100)
        self._max_hold_minutes.setValue(int(self._strategy_config["max_hold_minutes"]))
        self._universe_max_age.setValue(
            max(1, int(values.get("shadow_candidate_universe_max_age_seconds", 90)))
        )
        self._settings_status.setText("기존 비교 설정 편집 중 · 새 주도주 돌파 정책이 아닙니다. " + (
            "켜짐 · 주문 없이 후보만 기록합니다."
            if self._candidate_enabled.isChecked() else "꺼짐 · 기존 후보 기록은 보존됩니다."))
        self._settings_group.setEnabled(True)

    def _show_settings_failure(self, message: str) -> None:
        self._settings_status.setText(f"NAS 설정 연결 실패 · {message}")
        self._settings_group.setEnabled(True)

    def _clear_settings_worker(self, worker: CandidateSettingsWorker) -> None:
        if self._settings_worker is worker:
            self._settings_worker = None
        worker.deleteLater()
        self._ram_save.setEnabled(self._ram_selection_loaded and self._ram_supported and not self._ram_stopping)
        self._ram_reload.setEnabled(not self._ram_stopping)
        self._ram_refresh.setEnabled(not self._ram_stopping)
        self._update_source_check_button()
        self._update_position_source_buttons()

    def _save_operational_settings(self) -> None:
        if "policy_id" in self._strategy_config:
            try:
                config = LeaderBreakoutPolicy.from_dict(json.loads(self._leader_policy_editor.toPlainText())).to_dict()
            except (TypeError, ValueError) as error:
                self._settings_status.setText("정책 확인 필요 · " + str(error))
                return
            changes = {"shadow_candidate_enabled": self._candidate_enabled.isChecked(),
                "shadow_candidate_config": config}
            revision = getattr(self, "_operational_revision", None)
            if isinstance(revision, int):
                changes["expected_revision"] = revision
            self._start_settings_request(changes)
            return
        config = dict(self._strategy_config)
        rank_enabled = self._rank_persistence.isChecked()
        config.update({
            "lookback_bars": self._lookback_bars.value(),
            "buffer_bps": round(self._buffer_percent.value() * 100),
            "rank_persistence_enabled": rank_enabled,
            "rank_persistence_required": rank_enabled,
            "stop_loss_bps": round(self._stop_loss_percent.value() * 100),
            "target_bps": round(self._target_percent.value() * 100),
            "max_hold_minutes": self._max_hold_minutes.value(),
        })
        if rank_enabled:
            defaults = default_shadow_breakout_config().to_dict()
            for name in (
                "rank_top_k", "rank_window_seconds", "rank_max_gap_seconds",
                "rank_min_residency_seconds",
            ):
                if config.get(name) is None:
                    config[name] = defaults[name]
        changes: dict[str, object] = {
            "shadow_candidate_enabled": self._candidate_enabled.isChecked(),
            "shadow_candidate_config": config,
            "shadow_candidate_poll_seconds": 2,
            "shadow_candidate_universe_max_age_seconds": self._universe_max_age.value(),
        }
        revision = getattr(self, "_operational_revision", None)
        if isinstance(revision, int):
            changes["expected_revision"] = revision
        self._start_settings_request(changes)

    def stop(self) -> None:
        self._ram_stopping = True
        self._ram_position_generation += 1
        self._update_position_source_buttons()
        self._update_source_check_button()
        self._ram_timer.stop()
        self._settings.setValue("geometry", self.saveGeometry())
        if self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wake_for_shutdown()
            self._worker.wait(6000)
        if self._settings_worker is not None and self._settings_worker.isRunning():
            self._settings_worker.requestInterruption()
            finished = self._settings_worker.wait(11_000)
            if not finished and self._ram_position_read_pending:
                self._settings_worker.wait()  # Keep the local read owned until its actual drain.

    def closeEvent(self, event) -> None:
        if self._ram_position_read_pending:
            self._ram_position_generation += 1
            if self._settings_worker is not None:
                self._settings_worker.requestInterruption()
        self._settings.setValue("geometry", self.saveGeometry())
        event.ignore()
        self.hide()

    def _build_ram_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("시장·테마의 기존 관찰·보유 행동 비교입니다. 주도주 돌파의 진입 정책은 첫 탭에서 설정합니다.\n"
            "현재가·체결·매매 실행은 0B를 사용하며, 필요한 기존 분봉 계산도 함께 유지합니다. "
            "연구는 일봉·5분봉·1분봉 등으로 규칙과 자동매매 적용 방법을 검증합니다. 기존 초봉 자료는 보존합니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._ram_open_breakout = QPushButton("주도주 돌파 · 0B 판단 설정 열기")
        self._ram_open_breakout.clicked.connect(lambda: self._tabs.setCurrentIndex(0))
        layout.addWidget(self._ram_open_breakout)
        self._ram_enabled = QCheckBox("시장·테마 관찰 사용")
        self._ram_themes = QLineEdit()
        self._ram_themes.setPlaceholderText("테마 ID를 쉼표로 구분해 입력하세요")
        self._ram_venue = QComboBox(); self._ram_venue.addItems(("KRX", "NXT"))
        self._ram_recording = QCheckBox("판단 변화의 최근 결과 저장")
        self._ram_form = QGroupBox("관찰 대상 선택")
        form = QFormLayout(self._ram_form)
        form.addRow(self._ram_enabled)
        form.addRow("테마", self._ram_themes)
        form.addRow("거래소", self._ram_venue)
        form.addRow(self._ram_recording)
        thesis_group = QGroupBox("기존 분봉 연구 가설의 보유 행동 비교")
        thesis_layout = QVBoxLayout(thesis_group)
        self._ram_thesis_enabled = QCheckBox("선택한 기존 연구 가설 비교 · 주문 없음")
        thesis_layout.addWidget(self._ram_thesis_enabled)
        self._ram_thesis_details = QWidget()
        thesis_form = QFormLayout(self._ram_thesis_details)
        self._ram_thesis_db = QLineEdit()
        self._ram_thesis_db.setPlaceholderText("PC의 연구 DB · 저장된 선택을 재사용할 때는 비워 둡니다")
        choose = QPushButton("DB 선택")
        choose.clicked.connect(self._choose_thesis_database)
        path_row = QHBoxLayout(); path_row.addWidget(self._ram_thesis_db, 1); path_row.addWidget(choose)
        thesis_form.addRow("연구 DB", path_row)
        self._ram_thesis_run = QLineEdit(); self._ram_thesis_id = QLineEdit()
        self._ram_thesis_policy = QComboBox()
        for policy in ("PRICE_ONLY", "IMMEDIATE_EXIT", "REDUCE_ON_RISK", "CONFIRM_THEN_EXIT"):
            self._ram_thesis_policy.addItem("기존 보유 비교 · " + _ram_label(policy), policy)
        self._ram_thesis_policy.setCurrentIndex(-1)
        thesis_form.addRow("연구 실행 ID", self._ram_thesis_run)
        thesis_form.addRow("진입 가설 ID", self._ram_thesis_id)
        thesis_form.addRow("보유 비교 정책", self._ram_thesis_policy)
        thesis_layout.addWidget(self._ram_thesis_details)
        self._ram_thesis_details.setEnabled(False)
        self._ram_thesis_enabled.toggled.connect(self._ram_thesis_details.setEnabled)
        form.addRow(thesis_group)
        position_group = QGroupBox("포지션 목표·행동 비교")
        position_layout = QVBoxLayout(position_group)
        self._ram_position_enabled = QCheckBox("가져온 비교 자료 사용 · 주문 없음")
        self._ram_position_live = QCheckBox("기존 모의계좌 관측으로 수량 갱신 · 새 계좌 조회 없음")
        self._ram_position_load = QPushButton("비교 입력 JSON 가져오기")
        self._ram_position_load.clicked.connect(self._choose_position_document)
        self._ram_position_input_status = QLabel("자료 없음 · 실시간 계좌 연결은 아직 준비 중입니다.")
        self._ram_position_input_status.setWordWrap(True)
        self._ram_position_input_status.setTextFormat(Qt.TextFormat.PlainText)
        for widget in (self._ram_position_enabled, self._ram_position_live, self._ram_position_load, self._ram_position_input_status):
            position_layout.addWidget(widget)
        self._ram_position_sources = QWidget()
        source_form = QFormLayout(self._ram_position_sources)
        self._ram_position_db = QLineEdit(); self._ram_position_windows = QLineEdit()
        for label, field, title, pattern in (
                ("연구 DB", self._ram_position_db, "PC 연구 DB 선택", "SQLite (*.sqlite *.sqlite3 *.db);;모든 파일 (*)"),
                ("자료 선택 JSON", self._ram_position_windows, "완결 자료의 창 선택 JSON", "JSON (*.json)")):
            button = QPushButton("선택…")
            def choose(_checked=False, target=field, caption=title, file_filter=pattern):
                path, _ = QFileDialog.getOpenFileName(self, caption, "", file_filter)
                if path: target.setText(path)
            button.clicked.connect(choose)
            row = QHBoxLayout(); row.addWidget(field, 1); row.addWidget(button)
            source_form.addRow(label, row)
        self._ram_position_role = QComboBox()
        self._ram_position_role.addItem("학습 구간 (TRAIN)", "TRAIN")
        self._ram_position_role.addItem("검증 구간 (VALIDATION)", "VALIDATION")
        self._ram_position_role.setCurrentIndex(-1)
        source_form.addRow("자료 구간", self._ram_position_role)
        self._ram_position_source_load = QPushButton("완결 자료 가져오기")
        self._ram_position_source_clear = QPushButton("행동 자료 해제")
        self._ram_position_source_load.clicked.connect(lambda: self._start_ram_request('position_source'))
        self._ram_position_source_clear.clicked.connect(self._clear_position_outcomes)
        buttons = QHBoxLayout(); buttons.addWidget(self._ram_position_source_load); buttons.addWidget(self._ram_position_source_clear)
        source_form.addRow(buttons)
        position_layout.addWidget(self._ram_position_sources)
        hint = QLabel("연구 시뮬레이션 자료 · 목표·정책은 유지합니다. 가져온 뒤 저장/적용하세요.\n"
            "HOLD·EXIT 및 검증된 자동 목표의 ADD·REDUCE·REENTER 자료를 지원합니다.")
        hint.setWordWrap(True); position_layout.addWidget(hint)
        form.addRow(position_group)
        self._ram_selection_status = QLabel("관찰 선택을 불러오세요.")
        self._ram_selection_status.setWordWrap(True)
        form.addRow(self._ram_selection_status)
        self._ram_form.setEnabled(False)
        layout.addWidget(self._ram_form)
        self._ram_reload = QPushButton("선택 불러오기")
        self._ram_save = QPushButton("선택 저장 / 적용"); self._ram_save.setEnabled(False)
        self._ram_refresh = QPushButton("결과 새로고침")
        self._ram_view = QComboBox(); self._ram_view.addItems(("실시간 판단", "최근 저장 결과"))
        row = QHBoxLayout()
        for widget in (self._ram_reload, self._ram_save, self._ram_view, self._ram_refresh):
            row.addWidget(widget)
        layout.addLayout(row)
        self._ram_reload.clicked.connect(lambda: self._start_ram_request("selection"))
        self._ram_save.clicked.connect(self._save_ram_selection)
        self._ram_refresh.clicked.connect(lambda: self._start_ram_request("recorded" if self._ram_view.currentIndex() else "live"))
        self._ram_view.currentIndexChanged.connect(self._ram_view_changed)
        source_group = QGroupBox("저장된 가설의 원본 대조")
        source_layout = QVBoxLayout(source_group)
        source_row = QHBoxLayout()
        self._ram_source_window = QSpinBox(); self._ram_source_window.setRange(1, 60)
        self._ram_source_window.setValue(20); self._ram_source_window.setSuffix(" 분 조회")
        self._ram_source_check = QPushButton("확정 분봉 원본 대조")
        self._ram_source_check.setEnabled(False)
        self._ram_source_check.clicked.connect(lambda: self._start_ram_request("source_check"))
        source_row.addWidget(QLabel("대조 범위")); source_row.addWidget(self._ram_source_window)
        source_row.addWidget(self._ram_source_check); source_row.addStretch()
        source_layout.addLayout(source_row)
        source_note = QLabel("서버에 저장된 선택을 수동 대조합니다. 편집 중인 값은 포함되지 않습니다.")
        source_note.setWordWrap(True); source_layout.addWidget(source_note)
        self._ram_source_status = QLabel("연구 가설을 선택·저장한 뒤 대조하세요. 원본 확정·운영 승인은 별도입니다.")
        self._ram_source_status.setWordWrap(True)
        self._ram_source_status.setTextFormat(Qt.TextFormat.PlainText)
        self._ram_source_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        source_layout.addWidget(self._ram_source_status)
        layout.addWidget(source_group)
        self._ram_status = QLabel("관찰 상태를 불러오세요."); self._ram_status.setWordWrap(True)
        layout.addWidget(self._ram_status)
        self._ram_table = QTableWidget(0, 8)
        self._ram_table.setHorizontalHeaderLabels(("테마 / 종목 / 정책", "거래소", "시장", "대장", "추세 / 확산 / 상한", "행동 제안", "자료 상태", "판단 시각 / 유효성"))
        self._ram_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._ram_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((200, 60, 90, 80, 160, 100, 180, 200)):
            self._ram_table.setColumnWidth(column, width)
        layout.addWidget(self._ram_table)
        self._ram_position_status = QLabel("포지션 비교 자료 없음 · 기대값 미확인")
        self._ram_position_status.setWordWrap(True)
        self._ram_position_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._ram_position_status)
        self._ram_allocation_status = QLabel('동적 비중 자료 없음 · 현재 체결 0B / 기존 분봉 연구 비교 유지')
        self._ram_allocation_status.setWordWrap(True)
        self._ram_allocation_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._ram_allocation_status)
        self._ram_allocation_table = QTableWidget(0, 5)
        self._ram_allocation_table.setHorizontalHeaderLabels(('종목', '현재가(원)', '창 거래대금(원)', '관측초 가격 RMS(bp)', '관측 창(초)'))
        self._ram_allocation_table.resizeColumnToContents(3)
        self._ram_allocation_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._ram_allocation_table)
        self._ram_position_table = QTableWidget(0, 9)
        self._ram_position_table.setHorizontalHeaderLabels(("행동", "실제 / 확인된 미체결 매수·매도", "예상 / 목표",
            "추가 수량", "비용 차감 평균(원)", "HOLD 대비 차이(원)", "짝 표본", "차이 표준오차(원)", "자료 상태"))
        self._ram_position_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._ram_position_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((100, 175, 125, 100, 140, 145, 75, 160, 190)):
            self._ram_position_table.setColumnWidth(column, width)
        layout.addWidget(self._ram_position_table)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _tab_changed(self, _index):
        self._sync_polling_state()
        self._refresh_ram_if_visible()

    def _refresh_ram_if_visible(self):
        if self._ram_stopping or not self.isVisible() or self._tabs.currentIndex() != 1:
            return
        if self._ram_view.currentIndex() != 0:
            self._render_ram_results()
            return
        kind = "selection" if not self._ram_selection_loaded and self._operational_client is not None else "live"
        self._start_ram_request(kind)

    def _start_ram_request(self, kind, changes=None, *, prepare=None):
        if self._ram_stopping or (self._settings_worker is not None and self._settings_worker.isRunning()):
            return False
        if kind in {"selection", "save"}:
            if self._operational_client is None or not hasattr(self._operational_client, "load_ram_shadow_selection"):
                self._ram_status.setText("시장·테마 설정 연결을 사용할 수 없습니다.")
                return False
            if kind == "save":
                def request():
                    prepared = prepare() if prepare is not None else changes
                    if QThread.currentThread().isInterruptionRequested():
                        raise RuntimeError("관찰 설정 저장이 중단되었습니다.")
                    return self._operational_client.save_ram_shadow_selection(prepared)
            else:
                request = self._operational_client.load_ram_shadow_selection
            callback = self._apply_ram_selection
        elif kind == "source_check":
            if not self._can_check_ram_sources():
                return False
            revision = self._ram_revision
            thesis_ref = self._ram_saved_stock_thesis["evidence"]["thesis"]["thesis_id"]
            window = self._ram_source_window.value()
            def request():
                if QThread.currentThread().isInterruptionRequested():
                    raise RuntimeError("원본 대조가 중단되었습니다.")
                return self._content_client.check_ram_shadow_minute_sources(expected_revision=revision, lookback_minutes=window)
            callback = lambda values: self._apply_ram_source_check(values, revision, thesis_ref, window)
            self._ram_source_status.setText(f"저장된 선택 #{revision} · {window}분 조회 범위 대조 중…")
        elif kind == 'position_source':
            if self._settings_worker is not None or not self._ram_selection_loaded or not self._ram_supported:
                return False
            generation = self._ram_position_generation
            context = self._position_source_context()
            document = deepcopy(self._ram_position_document)
            request = lambda: _prepare_position_outcome_document(document, *context[1:])
            callback = lambda values: self._apply_position_source(values, generation, context, document)
            self._ram_position_read_pending = True
            self._ram_position_input_status.setText("완결 자료를 읽는 중… 기존 입력은 유지됩니다.")
        else:
            if not hasattr(self._content_client, "load_ram_shadow"):
                self._ram_status.setText("시장·테마 관찰 조회를 사용할 수 없습니다.")
                return False
            request = lambda: self._content_client.load_ram_shadow(include_recorded=kind == "recorded")
            callback = self._apply_ram_page
        worker = CandidateSettingsWorker(self._operational_client, parent=self, request=request)
        worker.settingsReady.connect(callback)
        if kind == 'position_source':
            worker.failed.connect(lambda message: self._position_source_failed(message, generation))
        else:
            worker.failed.connect(lambda message: self._show_ram_request_failure(kind, message))
        worker.finished.connect(lambda source=worker: self._finish_ram_request(source, kind))
        self._settings_worker = worker
        self._update_position_source_buttons()
        for button in (self._ram_save, self._ram_reload, self._ram_refresh, self._ram_source_check):
            button.setEnabled(False)
        worker.start()
        return True

    def _apply_ram_selection(self, values):
        if self._ram_stopping: return
        self._ram_position_generation += 1
        self._ram_selection_loaded = True
        self._ram_revision = values["revision"]
        self._ram_supported = bool(values.get("selection_supported", values.get("runtime_supported")))
        self._ram_enabled.setChecked(bool(values.get("enabled")))
        self._ram_themes.setText(", ".join(values.get("theme_ids", [])))
        self._ram_venue.setCurrentText(str(values.get("leadership_venue", "KRX")))
        self._ram_recording.setChecked(bool(values.get("recording")))
        self._ram_position_document = deepcopy(values.get("position_comparison"))
        self._ram_position_enabled.setChecked(self._ram_position_document is not None)
        self._ram_position_live.setChecked(values.get("position_account_live", False))
        self._ram_saved_position_live = self._ram_position_live.isChecked()
        self._ram_position_input_status.setText(
            ("저장된 목표·비교 정책 · 계좌 수량·기준가격은 기존 RAM 관측 사용" if self._ram_saved_position_live
             else "저장된 고정 비교 입력 · 실시간 계좌 자료 아님") if self._ram_position_document
            else "자료 없음 · 실시간 계좌 연결은 아직 준비 중입니다.")
        if self._ram_position_document and any(isinstance(row, dict) and
                str(row.get('input_ref', '')).startswith('native-position-outcome_')
                for row in self._ram_position_document.get('outcomes', ())):
            self._ram_position_input_status.setText("저장된 연구 시뮬레이션 비교 자료 · 원장 재조회 없음 · 주문 없음")
        self._ram_saved_stock_thesis = deepcopy(values.get("stock_thesis"))
        self._ram_thesis_enabled.setChecked(self._ram_saved_stock_thesis is not None)
        self._ram_thesis_db.clear()
        thesis = (self._ram_saved_stock_thesis or {}).get("evidence", {}).get("thesis", {})
        self._ram_thesis_run.setText(thesis.get("run_id", ""))
        self._ram_thesis_id.setText(thesis.get("thesis_id", ""))
        self._ram_thesis_policy.setCurrentIndex(self._ram_thesis_policy.findData(
            (self._ram_saved_stock_thesis or {}).get("thesis_policy_version")))
        self._ram_form.setEnabled(self._ram_supported)
        self._update_position_source_buttons()
        self._ram_save.setEnabled(self._ram_supported)
        state = "복구 필요" if values.get("apply_status") == "RECOVERY_REQUIRED" else "켜짐" if values.get("enabled") else "꺼짐"
        if values.get("mode") == "explicit_bindings":
            state = "서버에서 지정한 관찰 대상 · 화면 선택 변경 불가"
        elif not values.get("runtime_supported"):
            state = "기존 실시간 수집 연결이 필요합니다"
        self._ram_selection_status.setText("관찰 선택: " + state + (" · " + str(values["last_error"]) if values.get("last_error") else ""))
        self._ram_source_status.setText("저장된 선택을 불러왔습니다. 원본 대조는 버튼을 눌러 실행하세요. 운영 승인은 별도입니다.")
        self._update_source_check_button()

    def _can_check_ram_sources(self):
        return (self._ram_selection_loaded and self._ram_supported and self._ram_saved_stock_thesis is not None
                and not self._ram_stopping and callable(getattr(self._content_client, "check_ram_shadow_minute_sources", None)))

    def _update_source_check_button(self):
        busy = self._settings_worker is not None and self._settings_worker.isRunning()
        self._ram_source_check.setEnabled(self._can_check_ram_sources() and not busy)

    def _apply_ram_source_check(self, values, revision, thesis_ref, window):
        if self._ram_stopping:
            return
        current = (self._ram_saved_stock_thesis or {}).get("evidence", {}).get("thesis", {}).get("thesis_id")
        if not self._ram_selection_loaded or self._ram_revision != revision or current != thesis_ref:
            self._ram_source_status.setText("선택이 바뀌어 이전 대조 결과를 표시하지 않습니다. 저장된 선택을 다시 불러오세요.")
            return
        try:
            minute_check = values['version'] == 'stock-thesis-source-check/v2'
            if (values["version"] not in {"stock-thesis-source-check/v1", "stock-thesis-source-check/v2"} or values["selection_revision"] != revision
                    or values["thesis_ref"] != thesis_ref or values["lookback_minutes" if minute_check else "lookback_seconds"] != window
                    or values["scope"] != ("bounded_final_completed_minutes" if minute_check else "bounded_current_native_values")
                    or values["source_durability"] != "not_verified"
                    or values["replay_eligible"] is not False or values["order_authorized"] is not False):
                raise ValueError("대조 대상 또는 승인 범위가 다릅니다")
            as_of = datetime.fromisoformat(values["as_of"])
            if as_of.tzinfo is None or as_of.utcoffset() is None:
                raise ValueError("대조 판단 시각을 확인할 수 없습니다")
            theme, seconds = values["theme"], values["minutes" if minute_check else "seconds"]
            theme_label = {"MATCHED": "테마 내용 일치", "NOT_FOUND_IN_BOUNDED_READ": "조회 범위에서 테마를 찾지 못함",
                           "CONTENT_HASH_MISMATCH": "테마 내용 해시 불일치", "MEMBERSHIP_MISMATCH": "테마 구성원 불일치"}[theme["status"]]
            counts = (seconds["expected"], seconds["matched"], seconds["rows_read"], theme["rows_read"])
            if (any(type(n) is not int or n < 0 for n in counts) or counts[0] > 10000
                    or counts[1] > min(counts[0], counts[2]) or counts[2] > 10000 or counts[3] > 100):
                raise ValueError("대조 건수가 허용 범위를 벗어납니다")
            refs = values["unverified_refs"]
            lists = (seconds["unmatched_refs"], refs["leadership"], refs["factors"], refs["hypotheses"])
            if (any(not isinstance(v, list) or any(not isinstance(ref, str) for ref in v) for v in lists)
                    or len(lists[0]) != counts[0] - counts[1]):
                raise ValueError("미확인 참조 형식이 다릅니다")
            elapsed = values["timing_ms"]["total"]
            if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("대조 시간을 확인할 수 없습니다")
            budget = " · 테마 조회 한도 도달" if theme["budget_reached"] else ""
            unit = '분봉' if minute_check else '과거 초봉'
            second_label = f"{unit} {counts[1]}/{counts[0]} 일치" if counts[0] else f"{unit} 대조 가능한 참조 없음"
            self._ram_source_status.setText(
                f"저장된 선택 #{revision} · 판단 시각 {_display_time(values['as_of'])} · {window}{'분' if minute_check else '초'} 조회 범위\n"
                f"{theme_label}{budget} · {second_label} · 기타 미대조 참조 {sum(map(len, lists[1:]))}개\n"
                f"테마 {counts[3]}행 / {unit} {counts[2]}행 조회 · {elapsed:.1f}ms\n"
                f"{unit} 이전 이력·수집 연속성·요인/가설 출처 미확인 · 운영/재생 승인 없음")
        except (KeyError, TypeError, ValueError) as error:
            self._ram_source_status.setText("원본 대조 응답을 확인할 수 없습니다. 운영 승인은 별도입니다. " + str(error))

    def _save_ram_selection(self):
        if not self._ram_selection_loaded or not self._ram_supported:
            return
        themes = [v.strip() for v in self._ram_themes.text().split(",") if v.strip()]
        changes = {"expected_revision": self._ram_revision, "enabled": self._ram_enabled.isChecked(),
                   "theme_ids": themes, "leadership_venue": self._ram_venue.currentText(),
                   "recording": self._ram_recording.isChecked()}
        if self._ram_position_enabled.isChecked():
            if self._ram_position_document is None:
                self._show_ram_failure("포지션 비교 입력 JSON을 먼저 가져오세요.")
                return
            changes["position_comparison"] = deepcopy(self._ram_position_document)
            if self._ram_position_live.isChecked():
                changes["position_account_live"] = True
        elif self._ram_position_document is not None:
            changes["position_comparison"] = None
        if not self._ram_position_live.isChecked() or not self._ram_position_enabled.isChecked():
            # Explicit false clears a previously saved live source without changing defaults.
            if getattr(self, "_ram_saved_position_live", False):
                changes["position_account_live"] = False
        if self._ram_thesis_enabled.isChecked():
            # Capture widget values here; the worker only handles plain data/IO.
            selected = (self._ram_thesis_db.text().strip(), self._ram_thesis_run.text().strip(),
                        self._ram_thesis_id.text().strip(), self._ram_thesis_policy.currentData())
            previous = deepcopy(self._ram_saved_stock_thesis)
            def prepare():
                from kiwoom_monitor.central_server.market_theme_shadow_inputs import persisted_stock_thesis_shadow_request_from_evidence
                from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
                path, run_id, thesis_id, policy = selected
                if path:
                    evidence = ResearchRepository(Path(path), read_only=True).load_entry_thesis_evidence(run_id, thesis_id)
                elif previous is not None:
                    evidence = deepcopy(previous["evidence"])
                else:
                    raise ValueError("연구 DB와 진입 가설을 명시 선택하세요.")
                request = persisted_stock_thesis_shadow_request_from_evidence(evidence,
                    run_id=run_id, thesis_id=thesis_id, thesis_policy_version=policy,
                    leadership_venue=changes["leadership_venue"], decision_time=self._now().isoformat())
                if request.entry_thesis.theme_id not in changes["theme_ids"]:
                    raise ValueError("진입 가설의 테마를 관찰 대상에 포함하세요.")
                return {**changes, "stock_thesis": {"evidence": evidence, "thesis_policy_version": policy}}
            self._start_ram_request("save", changes, prepare=prepare)
            return
        if self._ram_saved_stock_thesis is not None:
            changes["stock_thesis"] = None
        self._start_ram_request("save", changes)

    def _choose_thesis_database(self):
        path, _ = QFileDialog.getOpenFileName(self, "연구 DB 선택", "", "연구 DB (*.sqlite3 *.sqlite *.db);;모든 파일 (*)")
        if path:
            self._ram_thesis_db.setText(path)

    def _choose_position_document(self):
        path, _ = QFileDialog.getOpenFileName(self, "고정 포지션 비교 입력 선택", "", "JSON (*.json)")
        if not path:
            return
        try:
            with Path(path).open("rb") as stream:
                raw = stream.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                raise ValueError("입력 파일은 256 KiB 이하여야 합니다.")
            document = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(document, dict) or document.get("schema") != "position-action-comparison-shadow/v1":
                raise ValueError("포지션 행동 비교 입력 형식이 아닙니다.")
            self._ram_position_document = document
            self._ram_position_generation += 1
            self._ram_position_enabled.setChecked(True)
            self._ram_position_input_status.setText(Path(path).name + " · 고정 자료 · 저장 / 적용 시 검증합니다.")
            self._update_position_source_buttons()
        except (OSError, ValueError) as error:
            self._ram_position_input_status.setText("가져오기 실패 · " + str(error))

    def _position_source_context(self):
        return (getattr(self, '_ram_revision', None), self._ram_position_db.text().strip(),
                self._ram_position_windows.text().strip(), self._ram_position_role.currentData())

    def _update_position_source_buttons(self):
        idle = self._settings_worker is None and not self._ram_stopping
        document = self._ram_position_document
        available = idle and self._ram_selection_loaded and self._ram_supported and isinstance(document, dict)
        self._ram_position_load.setEnabled(idle)
        self._ram_position_sources.setEnabled(available)
        self._ram_position_source_load.setEnabled(available and document.get('outcomes') == [] if available else False)
        self._ram_position_source_clear.setEnabled(available and bool(document.get('outcomes')) if available else False)

    def _clear_position_outcomes(self):
        if self._settings_worker is not None or self._ram_stopping or not isinstance(self._ram_position_document, dict):
            return
        self._ram_position_document = {**deepcopy(self._ram_position_document), 'outcomes': []}
        self._ram_position_generation += 1
        self._ram_position_input_status.setText("행동 자료를 해제했습니다. 목표·정책은 유지되며 저장/적용은 별도입니다.")
        self._update_position_source_buttons()

    def _position_source_failed(self, message, generation):
        if not self._ram_stopping and generation == self._ram_position_generation:
            self._ram_position_input_status.setText("완결 자료 가져오기 실패 · 기존 입력 유지 · " + message)

    def _apply_position_source(self, values, generation, context, previous):
        if self._ram_stopping: return
        if (generation != self._ram_position_generation or context != self._position_source_context()
                or previous != self._ram_position_document):
            self._ram_position_input_status.setText("선택이 바뀌어 이전 읽기 결과를 적용하지 않았습니다.")
            return
        self._ram_position_document = deepcopy(values['document'])
        self._ram_position_generation += 1
        self._ram_position_enabled.setChecked(True)
        receipt = values['receipt']
        role = "학습" if receipt['development_role']=='TRAIN' else "검증"
        self._ram_position_input_status.setText(
            f"{role} 시뮬레이션 자료 {receipt['accepted_count']}/{receipt['requested_count']}창 · "
            f"제외 {len(receipt['excluded'])}창 · 관측 기대값 확인 {values['ev_available_count']}개\n"
            "목표·정책이 자료와 다르면 기대값 미확인 · 저장/적용은 별도 · 주문 없음")
        self._ram_position_input_status.setToolTip(json.dumps(receipt, ensure_ascii=False, indent=2))
        self._update_position_source_buttons()

    def _show_ram_failure(self, message):
        self._ram_status.setText("시장·테마 연결 실패 · " + message)

    def _show_ram_request_failure(self, kind, message):
        if kind in {"selection", "save"}:
            self._ram_selection_loaded = False
            self._ram_form.setEnabled(False)
            self._ram_selection_status.setText("관찰 선택 연결 실패 · 다시 불러오세요. " + message)
            self._update_source_check_button()
        elif kind == "source_check":
            if not self._ram_stopping:
                self._ram_source_status.setText("원본 대조 실패 · 저장된 선택을 다시 불러온 뒤 재시도하세요. " + message)
        else:
            self._show_ram_failure(message)

    def _finish_ram_request(self, worker, kind):
        if kind == 'position_source' and self._settings_worker is worker:
            self._ram_position_read_pending = False
        self._clear_settings_worker(worker)
        if not self._ram_stopping and self.isVisible() and self._tabs.currentIndex() == 1:
            desired = "recorded" if self._ram_view.currentIndex() else "live"
            if (kind in {"selection", "save"} and self._ram_selection_loaded) or (kind == "live" and desired == "recorded"):
                QTimer.singleShot(0, lambda: self._start_ram_request(desired) if self.isVisible() and not self._ram_stopping else None)

    def _ram_view_changed(self, _index):
        self._render_ram_results()
        if self.isVisible() and self._tabs.currentIndex() == 1:
            self._start_ram_request("recorded" if self._ram_view.currentIndex() else "live")

    def _apply_ram_page(self, values):
        self._ram_page = values
        if values.get("recorded") is not None:
            self._ram_recorded = values["recorded"]["document"]
        self._render_ram_results()

    def _render_ram_results(self):
        recorded = self._ram_view.currentIndex() == 1
        envelope = self._ram_recorded if recorded else (self._ram_page or {}).get("live")
        self._render_position_comparison(envelope, recorded=recorded)
        self._render_position_allocation(envelope, recorded=recorded)
        self._ram_table.setRowCount(0)
        if not envelope:
            self._ram_status.setText("최근 저장 결과가 없습니다." if recorded else "관찰 결과 준비 중 · " + str((self._ram_page or {}).get("runtime", {}).get("state", "DISABLED")))
            return
        slots = envelope.get("results", [])
        if len(slots) > 32:
            self._show_ram_failure("관찰 결과 수가 허용 범위를 넘었습니다.")
            return
        for slot in slots:
            context = envelope["contexts"][slot["context_ref"]]
            decision = slot.get("decision")
            h1, h2 = context["market"], context["leadership"]
            inference = h2["inferences"]
            expiry = decision["valid_until"] if decision else context["expires_at"]
            try:
                expired = datetime.fromisoformat(expiry) <= self._now()
            except (ValueError, TypeError):
                expired = True
            market_text = _ram_label(h1["inferences"].get("market_type", "UNKNOWN"))
            observation = envelope.get("market_observed_changes", {}).get(slot.get("market_observed_changes_ref"))
            observation_tip = ()
            observation_status = ""
            if observation:
                try:
                    change_expired = (not recorded and observation.get("observed_at") is not None
                        and datetime.fromisoformat(observation["expires_at"]) <= self._now())
                except (ValueError, TypeError, KeyError):
                    change_expired = True
                status = "STALE" if change_expired else observation["quality"]["status"]
                pattern = "UNKNOWN" if change_expired else observation.get("pattern", "UNKNOWN")
                labels = {"LEADER_CONCENTRATION": "첫 순위 집중", "LEADER_REPLACEMENT": "첫 순위 교체",
                    "DISPERSED_ROTATION": "분산 순위 회전", "LEADER_RETAINED": "첫 순위 유지", "UNKNOWN": "미확인"}
                lookup = observation.get("lookup_change")
                market_text += "\n조회순위 변화: " + ({"CHANGED": "첫 순위 교체", "RETAINED": "첫 순위 유지"}.get(lookup["state"], "미확인")
                    if lookup and not change_expired else labels.get(pattern, "미확인"))
                observation_status = " / 변화 " + {"UNAVAILABLE": "미확인", "MISSING": "자료 없음"}.get(status, _ram_label(status))
                candidates = () if change_expired else observation.get("candidates", ())
                counts = {state: sum(row["state"] == state for row in candidates)
                          for state in ("RETAINED", "LEFT", "REAPPEARED")}
                observation_tip = ("TOP20 조회순위 관측 · 전체 시장/매수 승인 아님",
                    "관측 " + (_display_time(observation["observed_at"]) if observation.get("observed_at") else "미확인"),
                    "판단 " + _display_time(observation.get("as_of")),
                    "만료 " + (_display_time(observation["expires_at"]) if observation.get("expires_at") else "미확인"),
                    f"유지 {counts['RETAINED']} · 이탈 {counts['LEFT']} · 재등장 {counts['REAPPEARED']}",
                    *observation["quality"].get("reasons", ()), *observation.get("input_refs", ()))
                price = observation.get("price_trade_leadership")
                if price:
                    try:
                        price_expired = not recorded and price["quality"]["status"] == "COMPLETE" and datetime.fromisoformat(price["expires_at"]) <= self._now()
                    except (ValueError, TypeError, KeyError):
                        price_expired = True
                    state = "UNKNOWN" if price_expired else price["state"]
                    price_labels = {"UNKNOWN": "미확인", "NO_CANDIDATE": "조건 충족 후보 없음", "EMERGING": "새 후보 관측",
                        "LOST": "기존 후보 조건 이탈", "RETAINED": "후보 유지", "REPLACED": "후보 교체", "ROTATING_GROUP": "후보 일부 변화"}
                    candidate_codes = () if price_expired else price.get("current_candidates", ())
                    minute_primary = price.get("primary_timeframe") == "1m"
                    price_basis = '0B ' if price.get('input_resolution') == '0B' else '과거 관측 '
                    market_text += ("\n1분 가격·대금 후보: " if minute_primary else '\n'+price_basis+"가격·대금 주도 후보: ") + price_labels.get(state, "미확인")
                    if candidate_codes:
                        market_text += " (" + ", ".join(candidate_codes) + ")"
                    if minute_primary:
                        confirmation = price.get("five_minute_confirmation", {})
                        confirmed_state = "UNAVAILABLE" if price_expired else confirmation.get("status", "UNAVAILABLE")
                        market_text += "\n5분 지속: " + {"CONFIRMED": "확인", "NOT_CONFIRMED": "지속 근거 부족", "UNAVAILABLE": "미확인"}.get(confirmed_state, "미확인")
                        observation_tip += ("주 판단은 완료된 1분봉, 지속성은 최근 5분 중 가격·대금 우위 3분 이상",
                            "실시간 시장 관찰에도 확정 분봉을 사용하며, 현재가·체결·보호는 0B를 사용합니다.")
                        observation_tip += ("분봉 기준 " + _display_time(price.get("as_of")),)
                        if price.get("published_at"):
                            observation_tip += ("분봉 결과 반영 " + _display_time(price["published_at"]),)
                        for label, frame in price.get("timeframes", {}).items():
                            flow = frame.get("market_flow", {})
                            if not price_expired and frame["quality"]["status"] == "COMPLETE" and flow:
                                current, previous = flow["current"], flow["previous"]
                                direction = "증가" if current["trade_value_won"] > previous["trade_value_won"] else "감소" if current["trade_value_won"] < previous["trade_value_won"] else "유지"
                                text = f"{label} TOP20 관측: 가격 상승 {current['positive_price_ratio_ppm']/10000:.1f}% · 대금 {direction}"
                                if label == "1m":
                                    market_text += "\n" + text.replace("1m", "1분")
                                observation_tip += (text + f" · 대금 합계 {current['trade_value_won']:,}원",)
                    price_reason_labels = {
                        "price_trade_continuity_unconfirmed": "연속 수신 확인 부족",
                        "two_window_price_sample_insufficient": "두 비교 구간 가격 표본 부족",
                        "previous_price_sample_insufficient": "이전 구간 가격 표본 부족",
                        "current_price_sample_insufficient": "현재 구간 가격 표본 부족",
                        "current_price_observation_unavailable": "현재 구간 시세 없음",
                        "price_observation_stale": "최근 시세 유효기간 지남",
                        "trade_source_not_approved": "해당 거래소 수신 승인 없음",
                        "trade_source_history_unconfirmed": "연속 수신 시작시각 미확인",
                        "trade_source_history_insufficient": "수신 승인 뒤 비교 이력 부족",
                        "ram_retention_history_insufficient": "보존된 비교 이력 부족",
                        "ram_delivery_gap_in_window": "비교 구간 수신·전달 공백",
                        "price_trade_row_conversion_incomplete": "시세 변환 일부 미확인",
                        "membership_capture_incomplete": "TOP20 소속 자료 불완전",
                        "membership_trading_day_mismatch": "소속 자료 거래일 불일치",
                        "comparison_cohort_price_trade_incomplete": "비교 종목 전원의 자료가 갖춰지지 않음",
                        "membership_stale": "조회순위 소속 자료 만료",
                        "price_trade_observation_expired": "판단 시점 시세자료 만료",
                        "price_trade_inputs_unavailable": "가격·거래대금 자료 미확인",
                        "previous_minute_history_incomplete": "이전 비교 구간 분봉 부족",
                        "current_minute_history_incomplete": "현재 비교 구간 분봉 부족",
                        "comparison_cohort_minute_history_incomplete": "비교 종목 전원의 완료 분봉이 갖춰지지 않음",
                        "minute_bar_not_closed_actual": "미완성 또는 추정 대금 분봉",
                        "minute_bar_lineage_unconfirmed": "분봉 시점·출처 미확인",
                    }
                    coverage = price["quality"].get("coverage")
                    if coverage:
                        coverage_expired = price_expired or price.get("projection") == "expired" or price["quality"]["status"] == "STALE"
                        if coverage_expired:
                            market_text += "\n시세자료 확인 만료"
                        else:
                            market_text += f"\n판단시점 시세자료 {coverage['usable_members']}/{coverage['expected_members']}종목"
                    observation_tip += ("주도 후보는 조회순서와 독립된 최근 가격 반응·대금 우위로 판단",
                        "시세 범위 " + str(price.get("universe", "미확인")) + " / " + str(price.get("venue", "미확인")),
                        "가격·대금 만료 " + _display_time(price.get("expires_at")),
                        *(price_reason_labels.get(reason, reason) for reason in price["quality"].get("reasons", ())))
                    for metric in price.get("metrics", ()):
                        if metric.get("quality") == "PARTIAL":
                            observation_tip += (f"{metric['code']}: 이전 {metric['previous_observation_count']}건 / 현재 {metric['current_observation_count']}건 · "
                                + " · ".join(price_reason_labels.get(reason, reason) for reason in metric["reasons"]),)
                        if metric["code"] in candidate_codes:
                            observation_tip += (f"{metric['code']}: 가격 {metric['current_return_bps']}bps · "
                                f"관측 대금 {metric['current_trade_value_won']}원 · 대금 순위 {metric.get('current_trade_value_rank', '미확인')}",)
            values = (slot.get("theme_id", h2["facts"].get("theme_id", "")) + (" / " + decision["symbol"] + "\n" + _ram_label(decision["thesis_policy_version"]) if decision else ""),
                slot.get("leadership_venue", context["scope"]["leadership_venue"]),
                market_text,
                inference.get("displayed_leader_id") or "미확인",
                " / ".join(_ram_label(inference.get(key, "UNKNOWN")) for key in ("trend_state", "expansion_state", "limit_state")),
                _ram_label(decision["action"]) if decision else "관찰",
                "시장 " + _ram_label(h1["quality"]["status"]) + " / 테마 " + _ram_label(h2["quality"]["status"]) + observation_status,
                _display_time(context["decision_time"]) + " · " + ("만료" if expired else "유효"))
            row = self._ram_table.rowCount(); self._ram_table.insertRow(row)
            reasons = (*h1["quality"].get("reasons", ()), *h2["quality"].get("reasons", ()))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                leadership_tip = ()
                if column == 3 and h2['facts'].get('price_return_basis') == 'first_observed_second_last_received_price/v1':
                    leadership_tip = ('창 첫 관측 초의 마지막 수신가격 → 창 마지막 수신가격 기준 상승률',
                        '고가·거래대금·체결 수는 모든 수신 체결 반영')
                item.setToolTip("\n".join((*leadership_tip, *reasons, *context.get("evidence_refs", ()), *observation_tip)))
                self._ram_table.setItem(row, column, item)
        self._ram_table.resizeRowsToContents()
        runtime = (self._ram_page or {}).get("runtime", {})
        error = runtime.get("last_error") or runtime.get("recording", {}).get("last_error")
        self._ram_status.setText(("최근 저장 · " + _display_time(envelope.get("selected_at")) + " · 과거 관찰 결과" if recorded else "실시간 관찰 · " + str(runtime.get("state", ""))) + (" · " + str(error) if error else ""))

    def _render_position_allocation(self, envelope, *, recorded):
        self._ram_allocation_table.setRowCount(0)
        self._ram_allocation_status.setToolTip('')
        value = (envelope or {}).get('position_allocation')
        if not value:
            self._ram_allocation_status.setText('동적 비중 자료 없음 · 현재 체결 0B / 기존 분봉 연구 비교 유지')
            return
        try:
            if value.get('order_authorized') is not False or value.get('version') != 'dynamic-position-allocation-ram/v1':
                raise ValueError('비중 결과의 관찰 계약을 확인할 수 없습니다')
            basis = value.get('input_resolution')
            if not recorded and basis != '0B':
                raise ValueError('구형 집계 입력 · 실시간 0B 결과 필요')
            facts = value['facts']
            if not isinstance(facts, list) or len(facts) > 200:
                raise ValueError('비중 관측 종목 수 초과')
            expired = not recorded and datetime.fromisoformat(value['valid_until']) <= self._now()
            status = '자료 만료' if expired else {'BLOCKED':'계산 보류', 'CALCULATED':'계산됨'}.get(value['quality']['status'], '미확인')
            prefix = '최근 저장 · ' if recorded else ''
            resolution = '개별 0B 체결' if basis == '0B' else '과거 초봉 집계'
            reasons = tuple(value['quality'].get('reasons', ()))
            self._ram_allocation_status.setText(prefix+'동적 비중 · '+resolution+' · '+status+
                ' · '+_display_time(value['decision_time'])+'\n미정 근거는 보류 · 주문 승인 없음')
            details = [_ram_label(reason) for reason in reasons]
            goals = value.get('r4_action_comparison', {}).get('goals', ())
            if goals:
                details.append('기존 분봉의 조건부 모의 비교 · 연구 규칙의 실시간 적용 방법은 미정 · 주문 승인 없음')
                for goal in goals[:200]:
                    conditional = goal.get('model_support', {}).get('conditional_shadow')
                    if conditional:
                        details.append(str(goal.get('symbol', ''))+' 분봉 모의 비교: '+str(conditional['status']))
                        details.extend('분봉 모의 비교 조건: '+_ram_label(reason)
                                       for reason in conditional.get('reasons', ()))
            self._ram_allocation_status.setToolTip('\n'.join(details))
            for row in facts:
                index = self._ram_allocation_table.rowCount()
                self._ram_allocation_table.insertRow(index)
                values = (row['symbol'], row.get('reference_price_won'), row.get('recent_turnover_won'),
                          row.get('observed_close_return_rms_bps'), row.get('turnover_window_seconds'))
                for column, item in enumerate(values):
                    cell = QTableWidgetItem('미확인' if item is None else f'{item:,}' if type(item) is int else str(item))
                    details = list(row.get('input_refs', ()))
                    if column == 3 and row.get('volatility_basis') == 'last_received_price_per_observed_second/v1':
                        details[:0] = ['관측 초별 마지막 수신가격 사이 수익률의 RMS',
                            '가격 표본 '+str(row['volatility_sample_count'])+'개 · 무체결 초의 가격은 채우지 않습니다.']
                    cell.setToolTip('\n'.join(details))
                    self._ram_allocation_table.setItem(index, column, cell)
        except (ValueError, TypeError, KeyError) as error:
            self._ram_allocation_status.setText('동적 비중 자료 확인 실패 · '+str(error))

    def _render_position_comparison(self, envelope, *, recorded):
        self._ram_position_table.setRowCount(0)
        self._ram_position_status.setToolTip('')
        value = (envelope or {}).get("position_comparison")
        self._ram_position_status.setText("포지션 비교 자료 없음 · 기대값 미확인")
        if value is None:
            return
        try:
            result = value["result"]
            live_account = value["input_mode"] == "owner_ram_snapshot" and value["is_live_account"] is True
            if live_account and result is None:
                self._ram_position_status.setText("계좌 관측 준비 중 · 기대값 미확인 · " +
                    " / ".join(_ram_label(reason) for reason in value["quality"]["reasons"]))
                return
            if ((not live_account and (value["input_mode"] != "supplied_fixed_snapshot" or value["is_live_account"] is not False))
                    or result["policy_version"] != "position-action-comparison-shadow/v1"
                    or result["order_authorized"] is not False or result["execution_support"] != "shadow_only"):
                raise ValueError("고정 관찰 자료의 범위를 확인할 수 없습니다.")
            cut = datetime.fromisoformat(result["decision_time"])
            if cut.tzinfo is None or cut.utcoffset() is None:
                raise ValueError("자료 판단 시각을 확인할 수 없습니다.")
            actions = result["actions"]
            expired = live_account and datetime.fromisoformat(value["valid_until"]) <= self._now()
            if not 2 <= len(actions) <= 5 or len({r["action"] for r in actions}) != len(actions):
                raise ValueError("행동 비교 수가 허용 범위를 벗어납니다.")
            def number(value):
                if value is None:
                    return "미확인"
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("비교 수치가 올바르지 않습니다.")
                return f"{value:,.2f}" if type(value) is float else f"{value:,}"
            for action in actions:
                target = result["targets"][action["action"]]
                available = action["ev_status"] == "EMPIRICAL_ESTIMATE"
                estimate, delta = action.get("estimate"), action.get("paired_delta")
                values = (_ram_label(action["action"]),
                    " / ".join(number(target[key]) for key in ("actual_quantity", "pending_buy_quantity", "pending_sell_quantity")),
                    number(target["projected_quantity"]) + " / " + number(target["target_quantity"]),
                    (_ram_label(target["incremental_side"]) + " " if target["incremental_side"] else "") + number(target["incremental_quantity"]),
                    number(estimate["mean_net_change_won"]) if available else "미확인",
                    number(delta["mean_net_change_won"]) if available else "미확인", number(action["paired_sample_count"]),
                    number(delta["net_standard_error_won"]) if available else "미확인",
                    ("자료 만료 / " if expired else "") + ("관측 평균" if available else "기대값 미확인") + " / " + ("수량 계산됨" if target["quality"]["status"] == "CALCULATED" else "수량 차단"))
                row = self._ram_position_table.rowCount(); self._ram_position_table.insertRow(row)
                refs = [str(ref) for ref in target["input_refs"]]
                tooltip = "\n".join((*target["quality"]["reasons"], *action["reasons"],
                    *value.get("quality", {}).get("reasons", ()), *refs, result["decision_id"]))
                sizing = target.get('target_sizing')
                if sizing is not None:
                    bounds = sizing['parameters']
                    tooltip += (f"\n자동 목표 수량 · 목표 원화 {number(bounds['target_notional_won'])}원 · "
                        f"보유 한도 {number(bounds['max_position_notional_won'])}원 / {number(bounds['max_position_quantity'])}주 · "
                        f"남길 현금 {number(bounds['cash_buffer_won'])}원\n" + sizing['policy_ref'])
                for column, text in enumerate(values):
                    item = QTableWidgetItem(text); item.setToolTip(tooltip)
                    self._ram_position_table.setItem(row, column, item)
            self._ram_position_table.resizeRowsToContents()
            parameters = result["parameters"]
            selection = result.get('action_selection')
            selection_text = ''
            if selection is not None:
                if selection['order_authorized'] is not False or selection['risk_authority'] not in {
                        'supplied_research_constraint_only', 'native_observation_not_execution_approval'}:
                    raise ValueError('행동 권고의 관찰 범위를 확인할 수 없습니다.')
                selection_text = ('자료 만료 · 행동 권고 사용 불가 · ' if expired else
                    '주문 없는 행동 권고: ' + _ram_label(selection['action']) + ' · ')
                if selection['risk_path_required']:
                    selection_text += '보호 Risk·Execution 경로 우선 · '
                self._ram_position_status.setToolTip('\n'.join((
                    *selection['reasons'], selection['risk_input_ref'],
                    '공급된 연구 위험 제약 · 실제 Risk 승인 아님',
                    '표준오차 차감 점수는 신뢰구간·미래 기대값 보정 결과가 아님')))
                native = result.get('selection_inputs')
                if native is not None:
                    source_fields=('thesis_decision_ref', 'risk_snapshot_ref', 'account_revision')
                    capability_text=''
                    if 'required_data_capabilities' in native:
                        labels={'sampled_price':'현재 가격','stock_thesis':'타점 판단','theme_context':'테마 맥락',
                            'news_context':'뉴스 맥락','turnover_delta':'거래대금 변화'}
                        required=native['required_data_capabilities'];missing=native['missing_data_capabilities']
                        selection_text += '정책별 자료·Risk RAM 근거 · '
                        capability_text='\n정책 필수 자료: '+(', '.join(labels[name] for name in required) if required else '추가 맥락 요구 없음')
                        capability_text+='\n부족한 정책 자료: '+(', '.join(labels[name] for name in missing) if missing else '없음')
                        action_inputs=native.get('action_inputs',{})
                        for action,value in action_inputs.items():
                            names=', '.join(labels[name] for name in value['required_data_capabilities']) or '추가 자료 없음'
                            missing_names=', '.join(labels[name] for name in value['missing_data_capabilities'])
                            rejected='부족: '+missing_names if missing_names else '타점 조건 미충족' if value['reasons'] else '자료 조건 충족'
                            capability_text+='\n'+action+' 필수 자료: '+names+' · '+rejected
                        if 'stock_thesis' not in set(required)|{name for value in action_inputs.values() for name in value['required_data_capabilities']}:
                            source_fields=('risk_snapshot_ref','account_revision')
                    else:
                        selection_text += '기존 타점·Risk RAM 근거 · '
                    self._ram_position_status.setToolTip(self._ram_position_status.toolTip() + '\n' +
                        '\n'.join(str(native.get(name) or '미확인') for name in source_fields) +
                        capability_text+'\n기존 native 위험 관측 · 실행 승인 아님')
            else:
                self._ram_position_status.setToolTip('')
            self._ram_position_status.setText(("최근 저장 · " if recorded else "") + result["symbol"] +
                (" · 기존 모의계좌 RAM 관측 · 판단 시각 " if live_account else " · 고정 입력 판단 시각 ") + _display_time(result["decision_time"]) +
                f" · 비교 기간 {parameters['horizon_seconds']}초 · 최소 짝 표본 {parameters['minimum_samples']}개\n" +
                ("계좌 자료 시각 " + _display_time(value["account_as_of"]) +
                 (" · 계좌 전체 미체결 확인 · " if value.get("account_orders_complete") is True else " · 계좌 전체 미체결 근거 미확인 · ")
                 if live_account else "실시간 계좌 자료 아님 · ") +
                ("자동 목표 수량 정책 적용 · " if any(t.get('target_sizing') for t in result['targets'].values()) else "") +
                selection_text +
                "표본 독립성·확률 보정·원본 확정 미검증 · 주문 없음")
        except (KeyError, TypeError, ValueError) as error:
            self._ram_position_table.setRowCount(0)
            self._ram_position_status.setText("포지션 비교 응답을 확인할 수 없습니다 · " + str(error))


def _ram_label(value):
    return {"UNKNOWN": "미확인", "COMPLETE": "정상", "PARTIAL": "일부 자료", "STALE": "자료 지연",
            "HOLD": "유지 제안", "WAIT": "대기 제안", "REDUCE": "축소 제안", "EXIT": "청산 제안",
            "ADD": "추가 제안", "REENTER": "재진입 제안", "BUY": "매수", "SELL": "매도",
            "account_observation_not_ready": "기존 모의계좌 관측 없음",
            "ram_reference_price_unavailable": "수집된 KRX 기준가격 없음",
            "PRICE_EXIT": "가격 이탈", "WEAKENING": "약화", "NORMAL": "정상",
            "ADVANCING": "강화", "STABLE": "유지", "RECOVERED": "회복", "EXPANDING": "확산",
            "SLOWING": "둔화", "REACCELERATING": "재가속", "NONE": "해당 없음", "NEAR": "근접", "TOUCHED": "도달",
            "NORMAL_LEADER": "일반 대장", "SINGLE_LEADER": "단일 대장", "THEME_LED": "테마 주도", "DISPERSED_ROTATION": "분산 순환",
            "PRICE_ONLY": "가격 기준", "IMMEDIATE_EXIT": "즉시 청산", "REDUCE_ON_RISK": "위험 시 축소", "CONFIRM_THEN_EXIT": "확인 후 청산"}.get(str(value), str(value))


def _display_time(value: object) -> str:
    from kiwoom_monitor.infrastructure.time_display import format_kst_time
    return format_kst_time(value, "%m-%d %H:%M:%S")


def _quality_status(value: object) -> str:
    return {
        "DISABLED": "꺼짐",
        "WARMUP": "준비 중",
        "READY": "작동 중",
        "STALE": "자료 지연",
        "ERROR": "오류",
        "BLOCKED": "진입 보류",
    }.get(str(value), str(value))


def _quality_reason(value: object) -> str:
    reason = str(value)
    if reason.startswith('unconfigured:') or 'document_baseline_observation_only' in reason:
        return '주도주·진입·보호 조건 중 미정인 항목이 있어 진입을 보류합니다'
    exact = {
        "shadow_candidate_generation_disabled": "후보 감지가 꺼져 있습니다",
        "not_started": "감지 작업을 시작하는 중입니다",
        "candidate_universe_missing": "실시간 순위 자료를 기다리는 중입니다",
        "fresh": "자료가 정상입니다",
        "outside_krx_regular_strategy_session": "현재는 이 전략의 관찰 시간 밖입니다",
    }
    if reason.startswith("candidate_universe_stale:"):
        return "사용할 최신 순위 자료가 아직 없습니다"
    return exact.get(reason, reason)


def _leader_missing_label(name):
    return {'max_data_age_seconds':'자료 신선도 기준','signal_valid_seconds':'후보 유효시간',
        'minimum_turnover_won':'진입 거래대금 기준','structural_exit_rule':'구조 무효화 규칙',
        'hard_risk_profile_ref':'하드 위험 기준','minimum_confirmation_observations':'교차 확인 관측 수',
        'persistence_seconds':'교차 유지시간','breakout_buffer_bps':'돌파 여유 기준',
        'live_turnover_basis':'실시간 대금 비교 기준','research_turnover_basis':'연구 대금 비교 기준',
        'research_turnover_lookback_bars':'연구 대금 비교 창','leader_structure_context':'주도주·구조 근거',
        'reference_level.window_seconds':'기준가격 관찰 창','live_confirmation':'실시간 확인 방식',
        'research_confirmation':'분봉 확인 방식','leader_selection_evidence':'주도주 선정 근거'}.get(name,name)
