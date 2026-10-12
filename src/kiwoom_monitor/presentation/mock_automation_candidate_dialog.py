"""모의 후보 기준 동결·final 근거 준비·검토·게시 화면과 작업 수명."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
from typing import Any

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from scripts.prepare_mock_automation_candidate import (
    freeze_policy, load_publication, prepare_publication,
    inspect_final_preparation, final_preparation_text,
)
from scripts.summarize_historical_baseline_results import (
    development_review_text, inspect_development_baselines,
    daily_input_review_text, inspect_daily_development_inputs,
)


POLICY_INPUTS = (
    ("minimum_closed_trades", "완료 거래 최소(건)"),
    ("minimum_active_days", "운용일 최소(일)"),
    ("minimum_net_realized_pnl_won", "순실현손익 최소(원)"),
    ("maximum_drawdown_ppm", "낙폭 최대(ppm, 10000 = 1%)"),
)


class MockCandidateFileWorker(QThread):
    completed = Signal(str, dict)
    failed = Signal(str)

    def __init__(self, content, profile: dict, binding: dict, operation: str, values: dict, parent=None):
        super().__init__(parent)
        self._content = content
        self._profile = deepcopy(profile)
        self._binding = deepcopy(binding)
        self._operation = operation
        self._values = deepcopy(values)

    def _check_interruption(self):
        if self.isInterruptionRequested():
            raise RuntimeError("작업 종료를 요청했습니다. 새 게시 요청은 보내지 않습니다.")

    def execute(self) -> dict[str, Any]:
        self._check_interruption()
        values = self._values
        if self._operation == "development":
            return {"development_review": inspect_development_baselines(Path(values["development_root"]))}
        if self._operation == "inputs":
            return {"input_review": inspect_daily_development_inputs(
                Path(values["development_root"]), checkpoint=self._check_interruption,
                cost_reference=Path(values["cost_reference"]) if values.get("cost_reference") else None,
                gap_provenance=Path(values["gap_provenance"]) if values.get("gap_provenance") else None,
            )}
        if self._operation == "preparation":
            return {"preparation_review": inspect_final_preparation(
                Path(values["development_root"]), strategy_ref=values.get("strategy_ref", ""),
                final_start=values.get("final_start"), final_end=values.get("final_end"),
                request_path=Path(values["request"]) if values.get("request") else None,
                cost_reference=Path(values["cost_reference"]) if values.get("cost_reference") else None,
                gap_provenance=Path(values["gap_provenance"]) if values.get("gap_provenance") else None,
                checkpoint=self._check_interruption, **values["criteria"],
            )}
        account_ref = self._profile["account_ref"]
        if self._operation == "freeze":
            policy = freeze_policy(
                Path(values["request"]), Path(values["policy"]),
                strategy_ref=values["strategy_ref"], **values["criteria"],
            )
            return {"policy": policy.to_dict(), "tbd_fields": list(policy.tbd_fields)}
        if self._operation == "prepare":
            return {"documents": prepare_publication(
                Path(values["request"]), Path(values["policy"]), Path(values["publication"]),
                account_ref=account_ref, final_batch_id=values["batch_id"], final_run_id=values["run_id"],
            )}
        document = load_publication(Path(values["publication"]), account_ref=account_ref)
        if self._operation == "review":
            return {"documents": document}
        if self._operation != "publish":
            raise ValueError("지원하지 않는 후보 파일 작업입니다.")
        if document != values["reviewed_documents"]:
            raise ValueError("검토한 후보 파일이 변경됐습니다. 다시 검토하세요.")
        if document["eligibility_receipt"]["status"] != "ELIGIBLE":
            raise ValueError("적격 후보만 게시할 수 있습니다.")
        self._check_interruption()
        latest = self._content.load_mock_automation_candidates(
            account_ref, credential_profile_id=self._profile["profile_id"],
        )
        binding = latest.get("binding")
        fields = ("credential_profile_id", "broker", "environment", "account_ref", "binding_revision")
        expected = {name: self._binding.get(name) for name in fields}
        if (
            not isinstance(binding, dict)
            or {name: binding.get(name) for name in fields} != expected
            or expected["credential_profile_id"] != self._profile["profile_id"]
            or expected["account_ref"] != account_ref
            or expected["broker"] != "kiwoom" or expected["environment"] != "mock"
            or type(expected["binding_revision"]) is not int or expected["binding_revision"] < 1
        ):
            raise ValueError("모의계좌 연결이 변경됐습니다. 운용 화면에서 새로 확인하세요.")
        self._check_interruption()
        response = self._content.publish_mock_automation_candidate(
            account_ref=account_ref, credential_profile_id=self._profile["profile_id"],
            expected_binding_revision=expected["binding_revision"], **document,
        )
        if (response.get("status") not in {"saved", "unchanged"}
                or response.get("orders_started") is not False
                or response.get("package_hash") != document["package"]["package_hash"]
                or response.get("policy_id") != document["eligibility_policy"]["policy_id"]
                or response.get("receipt_id") != document["eligibility_receipt"]["receipt_id"]
                or response.get("eligibility_status") != "ELIGIBLE"):
            raise RuntimeError("게시 응답을 확인하지 못했습니다. 자동 재전송하지 않으며 NAS 목록을 다시 확인하세요.")
        return {"documents": document, "published": True}

    def run(self):
        try:
            self.completed.emit(self._operation, self.execute())
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as error:
            self.failed.emit(str(error))


def publication_summary(document: dict) -> str:
    package = document["package"]
    policy = document["eligibility_policy"]
    receipt = document["eligibility_receipt"]
    source = package["source_final"]
    spec = package["candidate_spec"]
    parameters = spec["parameters"]
    lines = [
        f"전략: {package['strategy_ref']} · {spec['family']}",
        f"판정: {'적격' if receipt['status'] == 'ELIGIBLE' else '차단'}",
        f"기준 동결: {policy['frozen_at']}",
        f"final 완료: {source['finished_at']}",
        f"final batch: {source['batch_id']}", f"final run: {source['run_id']}",
        "", "전략 조건",
    ]
    labels = {"lookback_bars": "기준 봉 수", "buffer_bps": "돌파 여유(bp)",
              "stop_loss_bps": "손절(bp)", "target_bps": "익절(bp)",
              "max_hold_minutes": "최대 보유(분)", "quantity": "수량(주)",
              "capital_won": "전략 자금(원)", "rank_persistence_enabled": "순위 지속성 사용",
              "rank_persistence_required": "순위 지속성 필수", "rank_top_k": "순위 상위 범위",
              "rank_window_seconds": "순위 관측(초)", "rank_max_gap_seconds": "순위 공백 최대(초)",
              "rank_min_residency_seconds": "순위 유지 최소(초)"}
    for name, label in labels.items():
        if name in parameters:
            value = parameters[name]
            lines.append(f"{label}: {'사용' if value is True else '미사용' if value is False else value}")
    lines.extend(["", "적격 기준"])
    for name, label in POLICY_INPUTS:
        value = policy[name]
        lines.append(f"{label}: {'미정' if value is None else value}")
    lines.extend(["", "관측 결과"])
    metric_labels = {"closed_trade_count": "완료 거래(건)", "active_day_count": "운용일(일)",
                     "net_realized_pnl_won": "순실현손익(원)", "max_drawdown_ppm": "최대 낙폭(ppm)"}
    for name, label in metric_labels.items():
        value = receipt["observed_metrics"].get(name)
        lines.append(f"{label}: {'미확인' if value is None else value}")
    costs = spec["execution_model"]["cost_model"]
    lines.extend(["", "연구 비용 모델", f"비용 근거: {costs.get('source', '')}",
                  f"비용 유효기간: {costs.get('valid_from', '')} ~ {costs.get('valid_to', '')}"])
    for name, label in (("commission_bps", "수수료(bp)"), ("sell_tax_bps", "매도세(bp)"), ("slippage_bps", "슬리피지(bp)")):
        lines.append(f"{label}: {costs.get(name, '미확인')}")
    if receipt["reasons"]:
        lines.extend(["", "차단 근거: " + ", ".join(receipt["reasons"])])
    return "\n".join(lines)


class MockAutomationCandidateDialog(QDialog):
    candidate_published = Signal()

    def __init__(self, content, profile: dict, binding: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("모의 후보 준비·게시")
        self.resize(820, 740)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._content = content
        self._profile = deepcopy(profile)
        self._binding = deepcopy(binding)
        self._worker: MockCandidateFileWorker | None = None
        self._reviewed_documents: dict | None = None
        self._inputs: dict[str, QLineEdit] = {}
        self._criteria: dict[str, QLineEdit] = {}
        self._status = QLabel("기준을 먼저 동결한 뒤 별도 final 평가를 완료하고 후보 파일을 준비하세요.")
        self._status.setWordWrap(True)
        form = QFormLayout()
        form.addRow("모의계좌", QLabel(str(profile.get("label") or profile["profile_id"])))
        form.addRow("계좌 식별자", QLabel(profile["account_ref"]))
        development_row = QHBoxLayout()
        development_row.addWidget(self._input("development_root"))
        development_browse = QPushButton("폴더 선택")
        development_browse.clicked.connect(self._browse_development)
        development_row.addWidget(development_browse)
        form.addRow("기존 개발 비교 결과", development_row)
        for name, label, save in (
            ("cost_reference", "비용 참고 근거(선택)", False),
            ("gap_provenance", "과거 공백 감사(선택)", False),
            ("request", "고정 연구 요청", False), ("policy", "동결 기준 파일", True),
            ("publication", "후보 게시 파일", True),
        ):
            edit = self._input(name)
            button = QPushButton("파일 선택")
            button.clicked.connect(lambda _checked=False, key=name, output=save: self._browse(key, output))
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(button)
            form.addRow(label, row)
        for name, label in (("strategy_ref", "전략 이름"), ("batch_id", "완료 final batch"), ("run_id", "완료 final run")):
            form.addRow(label, self._input(name))
        for name, label in (("final_start", "검토할 final 시작"), ("final_end", "검토할 final 끝(제외)")):
            edit = self._input(name)
            edit.setPlaceholderText("미정 · ISO 시각과 시간대(+09:00) 입력")
            form.addRow(label, edit)
        for name, label in POLICY_INPUTS:
            edit = QLineEdit()
            edit.setPlaceholderText("미정 · 값을 넣지 않으면 게시 차단")
            edit.textChanged.connect(self._invalidate_review)
            self._criteria[name] = edit
            form.addRow(label, edit)
        body = QWidget()
        body.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self._summary = QPlainTextEdit()
        self._summary.setReadOnly(True)
        self._buttons = {}
        development_button = QPushButton("돌파·눌림 개발 결과 확인")
        development_button.clicked.connect(lambda: self._request("development"))
        self._buttons["development"] = development_button
        inputs_button = QPushButton("일별 개발 비용·입력 점검")
        inputs_button.clicked.connect(lambda: self._request("inputs"))
        self._buttons["inputs"] = inputs_button
        preparation_button = QPushButton("후보·final 동결 준비 점검")
        preparation_button.clicked.connect(lambda: self._request("preparation"))
        self._buttons["preparation"] = preparation_button
        row = QHBoxLayout()
        for operation, label in (("freeze", "1. 기준 파일 동결"), ("prepare", "2. final 근거로 파일 준비"),
                                 ("review", "후보 파일 검토"), ("publish", "3. 검토한 후보 NAS 게시")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, action=operation: self._request(action))
            self._buttons[operation] = button
            row.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 2)
        layout.addWidget(development_button)
        layout.addWidget(inputs_button)
        layout.addWidget(preparation_button)
        layout.addWidget(self._summary, 2)
        layout.addWidget(self._status)
        layout.addLayout(row)
        self._set_busy(False)

    def _input(self, name):
        edit = QLineEdit()
        edit.textChanged.connect(self._invalidate_review)
        self._inputs[name] = edit
        return edit

    def _browse(self, name, save):
        if self._worker is not None:
            return
        chooser = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
        path, _ = chooser(self, "JSON 파일 선택", self._inputs[name].text(), "JSON (*.json)",
                          options=QFileDialog.Option.DontConfirmOverwrite if save else QFileDialog.Option(0))
        if path:
            self._inputs[name].setText(path)

    def _browse_development(self):
        if self._worker is None:
            path = QFileDialog.getExistingDirectory(self, "고정 개발 결과 폴더 선택", self._inputs["development_root"].text())
            if path:
                self._inputs["development_root"].setText(path)

    def _invalidate_review(self):
        if self._reviewed_documents is not None:
            self._status.setText("입력이 변경됐습니다. 후보 파일을 다시 검토하세요.")
        self._reviewed_documents = None
        if hasattr(self, "_buttons"):
            self._buttons["publish"].setEnabled(False)

    def _request(self, operation):
        if self._worker is not None:
            return
        values = {name: edit.text().strip() for name, edit in self._inputs.items()}
        required = ("development_root",) if operation in {"development", "inputs", "preparation"} else ("request", "policy", "strategy_ref") if operation == "freeze" else (
            ("request", "policy", "publication", "batch_id", "run_id") if operation == "prepare" else ("publication",)
        )
        if any(not values[name] for name in required):
            self._status.setText("선택한 작업에 필요한 파일과 참조를 입력하세요.")
            return
        try:
            if operation in {"freeze", "preparation"}:
                values["criteria"] = {name: int(edit.text().strip()) if edit.text().strip() else None
                                      for name, edit in self._criteria.items()}
            if operation == "publish":
                if self._reviewed_documents is None or self._reviewed_documents["eligibility_receipt"]["status"] != "ELIGIBLE":
                    raise ValueError("적격 후보 파일을 먼저 검토하세요.")
                values["reviewed_documents"] = self._reviewed_documents
        except ValueError as error:
            self._status.setText(f"입력 확인 필요 · {error}")
            return
        self._invalidate_review()
        self._set_busy(True)
        self._status.setText("후보 작업을 처리하는 중입니다…")
        worker = MockCandidateFileWorker(self._content, self._profile, self._binding, operation, values, self)
        self._worker = worker
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(lambda: self._finished(worker))
        worker.start()

    def _completed(self, operation, result):
        if operation == "development":
            self._reviewed_documents = None
            self._summary.setPlainText(development_review_text(result["development_review"]))
            self._status.setText("개발 원장 확인 완료 · final 후보를 선택하거나 적격으로 승격하지 않았습니다.")
        elif operation == "inputs":
            self._reviewed_documents = None
            self._summary.setPlainText(daily_input_review_text(result["input_review"]))
            self._status.setText("비용·입력 점검 완료 · 실제 비용과 종일 완전성은 추가 근거가 필요합니다.")
        elif operation == "preparation":
            self._reviewed_documents = None
            self._summary.setPlainText(final_preparation_text(result["preparation_review"]))
            self._status.setText("동결 준비 점검 완료 · 미정 기준·기간과 검증 잔여를 확인하세요.")
        elif operation == "freeze":
            policy = result["policy"]
            self._summary.setPlainText("기준 동결 시각: " + policy["frozen_at"] + "\n" + "\n".join(
                f"{label}: {'미정' if policy[name] is None else policy[name]}" for name, label in POLICY_INPUTS
            ))
            self._status.setText("기준 파일 동결 완료 · 미정 기준이 있어 게시 차단" if result["tbd_fields"] else "기준 파일 동결 완료 · 후보 적격 판정은 final 완료 후 확인합니다.")
        else:
            self._reviewed_documents = deepcopy(result["documents"])
            self._summary.setPlainText(publication_summary(result["documents"]))
            if result.get("published"):
                self._reviewed_documents = None
                self._status.setText("NAS 후보 게시 완료 · 주문은 시작되지 않았습니다.")
                self.candidate_published.emit()
            else:
                eligible = result["documents"]["eligibility_receipt"]["status"] == "ELIGIBLE"
                self._status.setText("후보 파일 검토 완료 · 적격, NAS 게시 가능" if eligible else "후보 파일 검토 완료 · 차단 근거를 확인하세요.")

    def _failed(self, message):
        self._reviewed_documents = None
        self._status.setText("후보 작업 실패 · " + message)

    def _finished(self, worker):
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
        self._set_busy(False)

    def _set_busy(self, busy):
        for edit in (*self._inputs.values(), *self._criteria.values()):
            edit.setEnabled(not busy)
        for operation, button in self._buttons.items():
            enabled = not busy
            if operation == "publish":
                enabled = enabled and self._reviewed_documents is not None and self._reviewed_documents["eligibility_receipt"]["status"] == "ELIGIBLE"
            button.setEnabled(enabled)

    def stop(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait()

    def closeEvent(self, event):
        if self._worker is not None:
            event.ignore()
            self._status.setText("진행 중인 작업 완료 후 닫을 수 있습니다.")
        else:
            super().closeEvent(event)

    def reject(self):
        if self._worker is None:
            super().reject()
