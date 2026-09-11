"""매매일지 전략팩 등록·추출·검토·승인 대화상자."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from kiwoom_monitor.application.strategy_pack import StrategyPackManifest
from kiwoom_monitor.application.strategy_pack_extraction import (
    COMPARISONS, RULE_CATEGORIES, STRATEGY_METRICS, ExtractedStrategyDraft,
    StrategyRuleDraft, data_burden, draft_change_labels, extend_reviewed_draft,
    extract_strategy_draft,
)
from kiwoom_monitor.application.trade_setup_classification import TRADE_SETUP_TYPES
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository

class StrategyPackExtractionWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, files: tuple[str, ...]) -> None:
        super().__init__(); self._files = files

    def run(self) -> None:
        try:
            self.completed.emit(extract_strategy_draft(tuple(Path(value) for value in self._files)))
        except Exception as error:
            self.failed.emit(str(error))


class StrategyRuleReviewDialog(QDialog):
    def __init__(self, pack: StrategyPackManifest, draft: ExtractedStrategyDraft,
                 parent: QWidget | None = None, previous: ExtractedStrategyDraft | None = None) -> None:
        super().__init__(parent); self.setWindowTitle(f"전략 규칙 검토 · {pack.name}"); self.resize(1100, 620)
        self._source_texts = draft.source_texts; layout = QVBoxLayout(self)
        summary = QLabel(f"추출 문단 {len(draft.rules)}개 · 원문 텍스트 {len(draft.source_texts)}개 파일을 로컬 매매일지 DB에 저장합니다.")
        layout.addWidget(summary)
        labels = draft_change_labels(previous, draft)
        self.table = QTableWidget(len(draft.rules), 10)
        self.table.setHorizontalHeaderLabels(("변경", "분류", "자동확인", "측정값", "비교", "기준값", "필요 데이터", "출처", "위치", "규칙 문장"))
        for row, rule in enumerate(draft.rules):
            category = QComboBox(); category.addItems(RULE_CATEGORIES); category.setCurrentText(rule.category)
            automatic = QCheckBox(); automatic.setChecked(rule.automatable)
            metric = QComboBox()
            for key, label in STRATEGY_METRICS.items(): metric.addItem(label, key)
            metric.setCurrentIndex(max(0, metric.findData(rule.metric_key)))
            comparison = QComboBox(); comparison.addItems(COMPARISONS); comparison.setCurrentText(rule.comparison)
            self.table.setItem(row, 0, QTableWidgetItem(labels[row]))
            self.table.setCellWidget(row, 1, category); self.table.setCellWidget(row, 2, automatic)
            self.table.setCellWidget(row, 3, metric); self.table.setCellWidget(row, 4, comparison)
            self.table.setItem(row, 5, QTableWidgetItem("" if rule.threshold is None else f"{rule.threshold:g}"))
            self.table.setItem(row, 6, QTableWidgetItem(", ".join(rule.required_data)))
            self.table.setItem(row, 7, QTableWidgetItem(rule.source_file)); self.table.setItem(row, 8, QTableWidgetItem(rule.location))
            self.table.setItem(row, 9, QTableWidgetItem(rule.text))
        self.table.setColumnWidth(3, 190); self.table.setColumnWidth(6, 150); self.table.setColumnWidth(9, 500)
        layout.addWidget(self.table)
        burden = QLabel("데이터 부담: " + " · ".join(f"{name}={state}" for name, state in data_burden(pack.required_data + pack.optional_data)))
        burden.setWordWrap(True); layout.addWidget(burden)
        note = QLabel("여기서는 추출 결과만 검토합니다. ‘자동확인’은 데이터로 검사할 가능성이 있다는 뜻이며, 아직 실제 판정식으로 승인된 것은 아닙니다.")
        note.setWordWrap(True); note.setStyleSheet("color:#666666;"); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("검토 내용 저장")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def draft(self) -> ExtractedStrategyDraft:
        rules = []
        for row in range(self.table.rowCount()):
            category = self.table.cellWidget(row, 1); automatic = self.table.cellWidget(row, 2)
            metric = self.table.cellWidget(row, 3); comparison = self.table.cellWidget(row, 4)
            raw_threshold = self.table.item(row, 5).text().strip()
            try: threshold = float(raw_threshold) if raw_threshold else None
            except ValueError: threshold = None
            rules.append(StrategyRuleDraft(
                category.currentText() if isinstance(category, QComboBox) else "설명",
                self.table.item(row, 9).text().strip(), self.table.item(row, 7).text(), self.table.item(row, 8).text(),
                tuple(item.strip() for item in self.table.item(row, 6).text().split(",") if item.strip()),
                automatic.isChecked() if isinstance(automatic, QCheckBox) else False,
                metric.currentData() if isinstance(metric, QComboBox) else "",
                comparison.currentText() if isinstance(comparison, QComboBox) else ">=", threshold,
            ))
        return ExtractedStrategyDraft(tuple(rule for rule in rules if rule.text), self._source_texts)


class StrategyPackDraftDialog(QDialog):
    """새 강의를 자동분석에 섞기 전 검토용 초안으로 등록한다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent); self.setWindowTitle("새 전략팩 초안"); self.resize(620, 330)
        layout = QVBoxLayout(self); form = QFormLayout()
        self.name = QLineEdit(); self.name.setPlaceholderText("예: ○○ 눌림매매 강의")
        self.setup_types = QComboBox(); self.setup_types.setEditable(True)
        self.setup_types.addItems(TRADE_SETUP_TYPES); self.setup_types.setCurrentText("돌파")
        self.setup_types.setToolTip("기본 유형을 선택하거나 여러 유형을 쉼표로 직접 입력할 수 있습니다.")
        self.required_data = QLineEdit(); self.required_data.setPlaceholderText("예: 1분봉, 일봉, VWAP, 거래량")
        self.optional_data = QLineEdit(); self.optional_data.setPlaceholderText("예: 뉴스, 수급, 호가")
        self.source_files = QLineEdit(); self.source_files.setReadOnly(True)
        choose = QPushButton("PDF·스크립트 선택"); choose.clicked.connect(self._choose_sources)
        source_row = QHBoxLayout(); source_row.addWidget(self.source_files); source_row.addWidget(choose)
        form.addRow("강의·전략 이름", self.name); form.addRow("주력 매매유형", self.setup_types)
        form.addRow("필수 데이터", self.required_data); form.addRow("선택 데이터", self.optional_data)
        form.addRow("강의 자료", source_row); layout.addLayout(form)
        notice = QLabel("등록한 자료는 아직 자동분석에 사용되지 않습니다. 다음 단계에서 추출된 규칙과 필요한 데이터를 검토한 뒤 활성화합니다.")
        notice.setWordWrap(True); notice.setStyleSheet("color:#666666;"); layout.addWidget(notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("검토 대기로 등록")
        buttons.accepted.connect(self._accept_if_valid); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _choose_sources(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self, "강의 PDF·스크립트 선택", "", "강의 자료 (*.pdf *.docx *.txt *.md);;모든 파일 (*)",
        )
        if selected:
            self.source_files.setText(" | ".join(selected))

    def _accept_if_valid(self) -> None:
        if not self.name.text().strip() or not self.source_files.text().strip():
            QMessageBox.information(self, "전략팩 초안", "전략 이름과 강의 자료를 선택해 주세요.")
            return
        self.accept()

    def manifest(self) -> StrategyPackManifest:
        name = self.name.text().strip()
        pack_id = "course_" + uuid.uuid5(uuid.NAMESPACE_URL, name + self.source_files.text()).hex[:12]
        split = lambda value: tuple(item.strip() for item in re.split(r"[,;/]", value) if item.strip())
        return StrategyPackManifest(
            pack_id=pack_id, name=name, version=1, enabled=False, priority=100,
            setup_types=split(self.setup_types.currentText()) or ("기타",),
            required_data=split(self.required_data.text()), optional_data=split(self.optional_data.text()),
            source_description="사용자가 등록한 강의 자료 · 규칙 검토 전",
            source_files=tuple(item.strip() for item in self.source_files.text().split("|") if item.strip()),
            review_status="draft",
        )


class StrategyPackManagerDialog(QDialog):
    def __init__(self, packs: tuple[StrategyPackManifest, ...], repository: JournalRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent); self.setWindowTitle("전략팩 관리"); self.resize(900, 430)
        self._packs = list(packs); self._repository = repository; self._worker: StrategyPackExtractionWorker | None = None
        self._pending_extension: tuple[int, StrategyPackManifest, ExtractedStrategyDraft | None, tuple[str, ...]] | None = None
        layout = QVBoxLayout(self)
        note = QLabel("검토 완료된 전략팩만 자동분석에 사용할 수 있습니다. 새 강의는 반드시 ‘검토 대기’로 등록됩니다.")
        note.setWordWrap(True); layout.addWidget(note)
        self.table = QTableWidget(0, 7); self.table.setHorizontalHeaderLabels(("사용", "전략팩", "상태", "우선순위", "매매유형", "필요 데이터", "자료"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); layout.addWidget(self.table)
        actions = QHBoxLayout(); add = QPushButton("새 전략팩"); extend = QPushButton("선택 전략팩에 강의 추가")
        extract = QPushButton("규칙 초안 만들기·보기"); approve = QPushButton("검증 후 승인")
        restore = QPushButton("이전 버전 복원"); remove = QPushButton("선택 초안 삭제")
        add.clicked.connect(self._add); extend.clicked.connect(self._add_sources); extract.clicked.connect(self._extract_or_review)
        approve.clicked.connect(self._approve); restore.clicked.connect(self._restore_version); remove.clicked.connect(self._remove)
        for button in (add, extend, extract, approve, restore, remove): actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        self.status = QLabel("대기"); self.status.setStyleSheet("color:#666666;"); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("전략팩 설정 반영")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self._render()

    def _render(self) -> None:
        self.table.setRowCount(len(self._packs))
        for row, pack in enumerate(self._packs):
            enabled = QTableWidgetItem(); enabled.setCheckState(Qt.CheckState.Checked if pack.enabled else Qt.CheckState.Unchecked)
            if pack.review_status != "approved":
                enabled.setFlags(enabled.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            name = QTableWidgetItem(f"{pack.name}  v{pack.version}"); name.setData(Qt.ItemDataRole.UserRole, pack.pack_id)
            priority = QSpinBox(); priority.setRange(1, 999); priority.setValue(pack.priority)
            status = {"approved": "사용 가능", "rules_reviewed": "규칙 검토 완료", "draft": "검토 대기"}.get(pack.review_status, pack.review_status)
            values = (enabled, name, QTableWidgetItem(status))
            for column, item in enumerate(values): self.table.setItem(row, column, item)
            self.table.setCellWidget(row, 3, priority)
            self.table.setItem(row, 4, QTableWidgetItem(", ".join(pack.setup_types)))
            self.table.setItem(row, 5, QTableWidgetItem(", ".join(pack.required_data)))
            self.table.setItem(row, 6, QTableWidgetItem(f"{len(pack.source_files)}개" if pack.source_files else pack.source_description))
        self.table.resizeColumnsToContents()

    def _add(self) -> None:
        dialog = StrategyPackDraftDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._packs.append(dialog.manifest()); self._render(); self.table.selectRow(len(self._packs) - 1)

    def _add_sources(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1" or self._worker is not None and self._worker.isRunning():
            return
        selected, _ = QFileDialog.getOpenFileNames(
            self, "추가 강의 PDF·스크립트 선택", "", "강의 자료 (*.pdf *.docx *.txt *.md);;모든 파일 (*)",
        )
        if not selected:
            return
        pack = self._packs[row]; previous = self._repository.load_strategy_pack_draft(pack.pack_id)
        additions = tuple(value for value in selected if value not in pack.source_files)
        if not additions:
            QMessageBox.information(self, "강의 추가", "이미 등록된 자료입니다."); return
        files = (*pack.source_files, *additions)
        missing = tuple(value for value in additions if not Path(value).is_file())
        if missing:
            QMessageBox.warning(self, "강의 추가", "강의 파일을 찾을 수 없습니다.\n" + "\n".join(missing)); return
        self._pending_extension = (row, pack, previous, files)
        self.status.setText(f"{pack.name} v{pack.version + 1} 변경 규칙을 비교하는 중…")
        self._worker = StrategyPackExtractionWorker(additions); self._worker.setParent(self)
        self._worker.completed.connect(lambda draft, target=row: self._extraction_completed(target, draft))
        self._worker.failed.connect(lambda message: self._extraction_failed(message)); self._worker.start()

    def _extract_or_review(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1":
            return
        pack = self._packs[row]
        saved = self._repository.load_strategy_pack_draft(pack.pack_id)
        if saved is not None:
            self._review(row, saved); return
        missing = tuple(value for value in pack.source_files if not Path(value).is_file())
        if missing:
            QMessageBox.warning(self, "전략팩 추출", "강의 파일을 찾을 수 없습니다.\n" + "\n".join(missing)); return
        self.status.setText(f"{pack.name} 텍스트와 규칙을 추출하는 중…")
        self._worker = StrategyPackExtractionWorker(pack.source_files); self._worker.setParent(self)
        self._worker.completed.connect(lambda draft, target=row: self._extraction_completed(target, draft))
        self._worker.failed.connect(lambda message: self._extraction_failed(message)); self._worker.start()

    def _extraction_completed(self, row: int, draft: object) -> None:
        if not isinstance(draft, ExtractedStrategyDraft): return
        previous = None
        if self._pending_extension is not None and self._pending_extension[0] == row:
            _, old_pack, previous, files = self._pending_extension
            self._repository.save_strategy_pack_version(old_pack, previous)
            draft = extend_reviewed_draft(previous, draft)
            self._packs[row] = StrategyPackManifest(**{
                **old_pack.to_dict(), "version": old_pack.version + 1, "source_files": files,
                "review_status": "draft", "enabled": False,
                "source_description": f"추가 강의 반영 · v{old_pack.version + 1} 재검토 필요",
            })
            self._pending_extension = None
        self._repository.save_strategy_pack_draft(self._packs[row].pack_id, draft)
        self.status.setText(f"추출 완료 · {len(draft.rules)}문단")
        self._render(); self.table.selectRow(row); self._review(row, draft, previous)

    def _extraction_failed(self, message: str) -> None:
        self._pending_extension = None
        self.status.setText("추출 실패"); QMessageBox.warning(self, "전략팩 추출", message)

    def _review(self, row: int, draft: ExtractedStrategyDraft, previous: ExtractedStrategyDraft | None = None) -> None:
        dialog = StrategyRuleReviewDialog(self._packs[row], draft, self, previous)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        reviewed = dialog.draft(); self._repository.save_strategy_pack_draft(self._packs[row].pack_id, reviewed)
        pack = self._packs[row]
        self._packs[row] = StrategyPackManifest(**{**pack.to_dict(), "review_status": "rules_reviewed", "enabled": False})
        self.status.setText(f"검토 저장 · {len(reviewed.rules)}문단"); self._render(); self.table.selectRow(row)

    def _restore_version(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1": return
        pack = self._packs[row]; versions = self._repository.load_strategy_pack_versions(pack.pack_id)
        if not versions:
            QMessageBox.information(self, "이전 버전 복원", "보관된 이전 버전이 없습니다."); return
        labels = [f"v{manifest.version} · {manifest.review_status} · 자료 {len(manifest.source_files)}개" for manifest, _ in versions]
        selected, accepted = QInputDialog.getItem(self, "이전 버전 복원", "복원할 버전", labels, 0, False)
        if not accepted: return
        index = labels.index(selected); old_manifest, old_draft = versions[index]
        if QMessageBox.question(self, "이전 버전 복원", f"{pack.name}을 v{old_manifest.version} 상태로 복원할까요?") != QMessageBox.StandardButton.Yes:
            return
        self._repository.save_strategy_pack_version(pack, self._repository.load_strategy_pack_draft(pack.pack_id))
        self._packs[row] = old_manifest
        if old_draft is not None: self._repository.save_strategy_pack_draft(pack.pack_id, old_draft)
        self.status.setText(f"v{old_manifest.version} 복원 완료"); self._render(); self.table.selectRow(row)

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1":
            return
        if self._packs[row].review_status == "approved":
            return
        removed = self._packs.pop(row); self._repository.delete_strategy_pack_draft(removed.pack_id); self._render()

    def _approve(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1": return
        pack = self._packs[row]; draft = self._repository.load_strategy_pack_draft(pack.pack_id)
        if draft is None:
            QMessageBox.information(self, "전략팩 승인", "먼저 규칙 초안을 만들고 검토해 주세요."); return
        automatic_entry = tuple(rule for rule in draft.rules if rule.category == "진입 조건" and rule.automatable)
        unmapped = tuple(rule for rule in automatic_entry if not rule.metric_key or rule.threshold is None)
        unsupported = tuple(name for name, state in data_burden(pack.required_data) if state == "지원 여부 검토 필요")
        realtime = tuple(name for name, state in data_burden(pack.required_data) if "승인 필요" in state)
        if not automatic_entry or unmapped or unsupported or realtime:
            details = []
            if not automatic_entry: details.append("자동 확인 가능한 진입 조건이 없습니다.")
            if unmapped: details.append(f"측정값·기준값 미연결 진입 조건 {len(unmapped)}개")
            if unsupported: details.append("지원 확인 필요 데이터: " + ", ".join(unsupported))
            if realtime: details.append("별도 실시간 저장 승인이 필요한 데이터: " + ", ".join(realtime))
            QMessageBox.warning(self, "전략팩 승인 불가", "\n".join(details)); return
        self._packs[row] = StrategyPackManifest(**{**pack.to_dict(), "review_status": "approved", "enabled": False})
        self.status.setText("승인 완료 · 사용하려면 사용 체크 후 설정을 저장하세요."); self._render(); self.table.selectRow(row)

    def packs(self) -> tuple[StrategyPackManifest, ...]:
        result = []
        for row, pack in enumerate(self._packs):
            enabled_item = self.table.item(row, 0); priority = self.table.cellWidget(row, 3)
            result.append(StrategyPackManifest(
                **{**pack.to_dict(),
                   "enabled": bool(enabled_item and enabled_item.checkState() == Qt.CheckState.Checked and pack.review_status == "approved"),
                   "priority": priority.value() if isinstance(priority, QSpinBox) else pack.priority}
            ))
        return tuple(result)
