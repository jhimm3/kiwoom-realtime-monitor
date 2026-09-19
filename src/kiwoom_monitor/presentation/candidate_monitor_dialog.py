from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QSettings, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
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

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                page = self._client.load_candidate_events(
                    after_sequence=self._cursor,
                    limit=1000,
                )
                page["initial_sync"] = self._initial
                next_cursor = page.get("next_cursor")
                if next_cursor is not None:
                    self._cursor = max(self._cursor, int(next_cursor))
                if not bool(page.get("has_more")):
                    self._cursor = max(self._cursor, int(page.get("high_watermark", self._cursor)))
                    self._initial = False
                page["consumed_sequence"] = self._cursor
                self.pageReceived.emit(page)
            except (RuntimeError, ValueError, OSError) as error:
                self.failed.emit(str(error))
            self.msleep(self._poll_milliseconds)


class CandidateSettingsWorker(QThread):
    settingsReady = Signal(dict)
    failed = Signal(str)

    def __init__(
        self, client: CentralOperationalSettingsClient, changes: dict[str, object] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._changes = changes

    def run(self) -> None:
        try:
            values = self._client.load() if self._changes is None else self._client.save(self._changes)
            self.settingsReady.emit(values)
        except (RuntimeError, ValueError, OSError) as error:
            self.failed.emit(str(error))


class CandidateMonitorDialog(QDialog):
    """PC-local consumer; NAS generation continues independently of this window."""

    def __init__(
        self, client: CentralContentClient,
        operational_client: CentralOperationalSettingsClient | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shadow 후보")
        self.resize(760, 650)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._settings = QSettings("KiwoomMonitor", "CandidateMonitor")
        self._known_events: set[str] = set()
        self._operational_client = operational_client
        self._settings_worker: CandidateSettingsWorker | None = None
        self._operational_settings_loaded = False
        self._strategy_config = default_shadow_breakout_config().to_dict()

        self._quality = QLabel("NAS 후보 상태를 확인하는 중입니다…")
        self._alert_enabled = QCheckBox("새 후보 화면·소리 알림")
        self._alert_enabled.setChecked(
            str(self._settings.value("alert_enabled", "0")) == "1"
        )
        self._alert_enabled.toggled.connect(
            lambda checked: self._settings.setValue("alert_enabled", "1" if checked else "0")
        )
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

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._settings_group)
        layout.addWidget(self._table)

        self._worker = CandidatePollWorker(client, self)
        self._worker.pageReceived.connect(self._apply_page)
        self._worker.failed.connect(self._show_failure)
        self._worker.start()
        if self._operational_client is None:
            self._settings_status.setText("NAS 운영 설정 연결을 사용할 수 없습니다.")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._operational_settings_loaded and self._operational_client is not None:
            self._start_settings_request()

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
        if self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(6000)
        if self._settings_worker is not None and self._settings_worker.isRunning():
            self._settings_worker.requestInterruption()
            self._settings_worker.wait(11_000)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


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
