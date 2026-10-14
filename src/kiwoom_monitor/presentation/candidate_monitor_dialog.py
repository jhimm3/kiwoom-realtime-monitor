from __future__ import annotations

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
    QApplication, QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QTabWidget, QWidget, QComboBox, QLineEdit, QFileDialog, QScrollArea,
)

from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
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
        self._operational_client = operational_client
        self._settings_worker: CandidateSettingsWorker | None = None
        self._operational_settings_loaded = False
        self._strategy_config = default_shadow_breakout_config().to_dict()
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
        self._alert_enabled = QCheckBox("새 후보 화면·소리 알림")
        self._alert_enabled.setChecked(
            str(self._settings.value("alert_enabled", "0")) == "1"
        )
        self._alert_enabled.toggled.connect(self._on_alert_enabled_changed)
        top = QHBoxLayout()
        top.addWidget(self._quality, 1)
        top.addWidget(self._alert_enabled)

        self._candidate_enabled = QCheckBox("후보 감지 사용")
        self._strategy_name = QLabel("완료봉 돌파 v1")
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
        self._settings_group = QGroupBox("후보 감지 설정")
        settings_form = QFormLayout(self._settings_group)
        settings_form.addRow("작동", self._candidate_enabled)
        settings_form.addRow("전략", self._strategy_name)
        settings_form.addRow("돌파 기준 봉", self._lookback_bars)
        settings_form.addRow("돌파 여유", self._buffer_percent)
        settings_form.addRow("관심순위", self._rank_persistence)
        settings_form.addRow("손절 기준", self._stop_loss_percent)
        settings_form.addRow("목표 기준", self._target_percent)
        settings_form.addRow("최대 관찰", self._max_hold_minutes)
        settings_form.addRow("순위 자료 유효시간", self._universe_max_age)
        settings_form.addRow(self._settings_status)
        settings_form.addRow(self._save_settings)
        self._settings_group.setEnabled(False)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(("감지 시각", "종목", "기준가", "수량", "만료", "상태"))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)

        candidate_page = QWidget()
        candidate_layout = QVBoxLayout(candidate_page)
        candidate_layout.addLayout(top)
        candidate_layout.addWidget(self._settings_group)
        candidate_layout.addWidget(self._table)
        self._tabs = QTabWidget()
        self._tabs.addTab(candidate_page, "매매 후보")
        self._tabs.addTab(self._build_ram_page(), "시장·테마 관찰")
        self._tabs.currentChanged.connect(self._tab_changed)
        layout = QVBoxLayout(self)
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
        quality = page.get("quality", {})
        status = _quality_status(quality.get("status", "UNKNOWN"))
        reason = _quality_reason(quality.get("reason", ""))
        self._quality.setText(f"NAS 후보: {status}" + (f" · {reason}" if reason else ""))
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
            if not event_id or event_id in self._known_events:
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

    def _append_event(self, event: dict[str, Any]) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        values = (
            _display_time(event.get("available_at")),
            str(event.get("symbol", "")),
            f"{int(event.get('signal_reference_price', 0)):,}",
            str(event.get("quantity", "")),
            _display_time(event.get("expires_at")),
            str(event.get("status", "")),
        )
        for column, value in enumerate(values):
            self._table.setItem(row, column, QTableWidgetItem(value))
        self._table.scrollToBottom()

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
        self._settings_status.setText(
            "켜짐 · 주문 없이 후보만 기록합니다."
            if self._candidate_enabled.isChecked() else "꺼짐 · 기존 후보 기록은 보존됩니다."
        )
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
        note = QLabel("시장·테마를 주문 없이 관찰합니다. 수집되지 않은 종목은 자료를 기다립니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
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
        thesis_group = QGroupBox("저장된 연구 가설의 행동 제안")
        thesis_layout = QVBoxLayout(thesis_group)
        self._ram_thesis_enabled = QCheckBox("선택한 연구 가설 사용 · 주문 없음")
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
            self._ram_thesis_policy.addItem(_ram_label(policy), policy)
        self._ram_thesis_policy.setCurrentIndex(-1)
        thesis_form.addRow("연구 실행 ID", self._ram_thesis_run)
        thesis_form.addRow("진입 가설 ID", self._ram_thesis_id)
        thesis_form.addRow("행동 정책", self._ram_thesis_policy)
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
        self._ram_source_window.setValue(20); self._ram_source_window.setSuffix(" 초")
        self._ram_source_check = QPushButton("저장된 가설 원본 대조")
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
                return self._content_client.check_ram_shadow_sources(expected_revision=revision, lookback_seconds=window)
            callback = lambda values: self._apply_ram_source_check(values, revision, thesis_ref, window)
            self._ram_source_status.setText(f"저장된 선택 #{revision} · {window}초 범위 대조 중…")
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
                and not self._ram_stopping and callable(getattr(self._content_client, "check_ram_shadow_sources", None)))

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
            if (values["version"] != "stock-thesis-source-check/v1" or values["selection_revision"] != revision
                    or values["thesis_ref"] != thesis_ref or values["lookback_seconds"] != window
                    or values["scope"] != "bounded_current_native_values"
                    or values["source_durability"] != "not_verified"
                    or values["replay_eligible"] is not False or values["order_authorized"] is not False):
                raise ValueError("대조 대상 또는 승인 범위가 다릅니다")
            as_of = datetime.fromisoformat(values["as_of"])
            if as_of.tzinfo is None or as_of.utcoffset() is None:
                raise ValueError("대조 판단 시각을 확인할 수 없습니다")
            theme, seconds = values["theme"], values["seconds"]
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
            second_label = f"초봉 {counts[1]}/{counts[0]} 일치" if counts[0] else "초봉 대조 가능한 참조 없음"
            self._ram_source_status.setText(
                f"저장된 선택 #{revision} · 판단 시각 {_display_time(values['as_of'])} · {window}초 범위\n"
                f"{theme_label}{budget} · {second_label} · 기타 미대조 참조 {sum(map(len, lists[1:]))}개\n"
                f"테마 {counts[3]}행 / 초봉 {counts[2]}행 조회 · {elapsed:.1f}ms\n"
                "초봉 이전 이력·수집 연속성·요인/가설 출처 미확인 · 운영/재생 승인 없음")
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
                    leadership_venue=changes["leadership_venue"], decision_time=datetime.now().astimezone().isoformat())
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
                expired = datetime.fromisoformat(expiry) <= datetime.now().astimezone()
            except (ValueError, TypeError):
                expired = True
            values = (slot.get("theme_id", h2["facts"].get("theme_id", "")) + (" / " + decision["symbol"] + "\n" + _ram_label(decision["thesis_policy_version"]) if decision else ""),
                slot.get("leadership_venue", context["scope"]["leadership_venue"]),
                _ram_label(h1["inferences"].get("market_type", "UNKNOWN")),
                inference.get("displayed_leader_id") or "미확인",
                " / ".join(_ram_label(inference.get(key, "UNKNOWN")) for key in ("trend_state", "expansion_state", "limit_state")),
                _ram_label(decision["action"]) if decision else "관찰",
                "시장 " + _ram_label(h1["quality"]["status"]) + " / 테마 " + _ram_label(h2["quality"]["status"]),
                _display_time(context["decision_time"]) + " · " + ("만료" if expired else "유효"))
            row = self._ram_table.rowCount(); self._ram_table.insertRow(row)
            reasons = (*h1["quality"].get("reasons", ()), *h2["quality"].get("reasons", ()))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip("\n".join((*reasons, *context.get("evidence_refs", ()))))
                self._ram_table.setItem(row, column, item)
        self._ram_table.resizeRowsToContents()
        runtime = (self._ram_page or {}).get("runtime", {})
        error = runtime.get("last_error") or runtime.get("recording", {}).get("last_error")
        self._ram_status.setText(("최근 저장 · " + _display_time(envelope.get("selected_at")) + " · 과거 관찰 결과" if recorded else "실시간 관찰 · " + str(runtime.get("state", ""))) + (" · " + str(error) if error else ""))

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
            expired = live_account and datetime.fromisoformat(value["valid_until"]) <= datetime.now().astimezone()
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
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


def _quality_status(value: object) -> str:
    return {
        "DISABLED": "꺼짐",
        "WARMUP": "준비 중",
        "READY": "작동 중",
        "STALE": "자료 지연",
        "ERROR": "오류",
    }.get(str(value), str(value))


def _quality_reason(value: object) -> str:
    reason = str(value)
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
