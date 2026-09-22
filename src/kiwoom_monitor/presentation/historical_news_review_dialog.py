"""Qt review surface for retrospective news relevance and event labels."""

from __future__ import annotations

import getpass
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kiwoom_monitor.application.historical_news_review_decisions import (
    build_historical_news_review_decisions,
    export_historical_news_review_sheet,
    load_historical_news_review_sheet,
    update_historical_news_review_sheet_row,
    write_historical_news_review_decisions,
)
from kiwoom_monitor.application.historical_news_review_queue import (
    HistoricalNewsReviewQueue,
    load_historical_news_review_queue,
)


_SEOUL = ZoneInfo("Asia/Seoul")
_DECISIONS = (
    ("미검토", ""),
    ("관련", "relevant"),
    ("무관", "not_relevant"),
    ("보류", "uncertain"),
)


class HistoricalNewsReviewDialog(QDialog):
    """Review one immutable queue through a resumable working CSV."""

    def __init__(self, research_dir: Path, parent=None, *, theme_repository: object | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("과거 뉴스 사람 검토")
        self.resize(1180, 720)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._research_dir = Path(research_dir)
        self._theme_repository = theme_repository
        self._queue: HistoricalNewsReviewQueue | None = None
        self._sheet_path: Path | None = None
        self._rows: list[dict[str, str]] = []
        self._items_by_id: dict[str, dict[str, Any]] = {}
        self._loading = False
        self._dirty = False
        self._current_row = -1

        self._status = QLabel("대기열을 확인하는 중입니다.")
        self._status.setWordWrap(True)
        self._progress = QLabel()

        refresh = QPushButton("다시 불러오기")
        refresh.clicked.connect(self.reload)
        self._finalize = QPushButton("검토 결과 동결")
        self._finalize.clicked.connect(self._finalize_decisions)
        top = QHBoxLayout()
        top.addWidget(self._status, 1)
        top.addWidget(self._progress)
        top.addWidget(refresh)
        top.addWidget(self._finalize)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(("상태", "발행시각", "종목", "기사 제목", "규칙 점수"))
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        self._table.setColumnWidth(0, 70)
        self._table.setColumnWidth(1, 160)
        self._table.setColumnWidth(2, 130)
        self._table.setColumnWidth(4, 75)
        self._table.currentCellChanged.connect(self._selection_changed)

        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._summary = QPlainTextEdit()
        self._summary.setReadOnly(True)
        self._summary.setMaximumBlockCount(200)
        self._summary.setPlaceholderText("검색 요약이 없습니다.")
        self._source = QLabel()
        self._source.setWordWrap(True)
        self._source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._open_article = QPushButton("원문 열기")
        self._open_article.clicked.connect(self._open_current_article)

        self._decision = QComboBox()
        for label, value in _DECISIONS:
            self._decision.addItem(label, value)
        self._decision.currentIndexChanged.connect(self._decision_changed)
        self._event = QComboBox()
        self._event.setEditable(True)
        self._event.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._event.lineEdit().setPlaceholderText("관련 기사들을 같은 사건 ID로 묶습니다.")
        self._profile = QComboBox()
        self._profile.setEditable(False)
        self._themes = QLineEdit()
        self._themes.setPlaceholderText("테마 여러 개는 | 로 구분")
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText("판정 근거나 확인할 점")
        self._notes.setMaximumHeight(90)
        self._reviewer = QLineEdit(getpass.getuser())

        form = QFormLayout()
        form.addRow("사람 판정", self._decision)
        form.addRow("사건 ID", self._event)
        form.addRow("테마 프로필", self._profile)
        form.addRow("테마 이름", self._themes)
        form.addRow("검토 메모", self._notes)
        form.addRow("검토자", self._reviewer)

        self._save = QPushButton("현재 판정 저장")
        self._save.clicked.connect(lambda: self._save_current(show_message=True, advance=False))
        clear = QPushButton("현재 판정 지우기")
        clear.clicked.connect(self._clear_current)
        next_pending = QPushButton("다음 미검토")
        next_pending.clicked.connect(self._select_next_pending)
        actions = QHBoxLayout()
        actions.addWidget(self._open_article)
        actions.addStretch()
        actions.addWidget(clear)
        actions.addWidget(self._save)
        actions.addWidget(next_pending)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.addWidget(self._title)
        detail_layout.addWidget(self._source)
        detail_layout.addWidget(self._summary, 1)
        detail_layout.addLayout(form)
        detail_layout.addLayout(actions)

        splitter = QSplitter()
        splitter.addWidget(self._table)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)

        for field in (
            self._event.lineEdit(), self._themes, self._notes, self._reviewer,
        ):
            if hasattr(field, "textChanged"):
                field.textChanged.connect(self._mark_dirty)
        self._profile.currentIndexChanged.connect(self._mark_dirty)
        self._populate_profiles()
        self.reload()

    @property
    def sheet_path(self) -> Path | None:
        return self._sheet_path

    def reload(self) -> None:
        if self._dirty and not self._save_current(show_message=False, advance=False):
            return
        try:
            _queue_path, queue = self._latest_queue()
            sheet = self._working_sheet(queue)
            rows = list(load_historical_news_review_sheet(queue, sheet))
        except (OSError, ValueError) as error:
            self._queue = None
            self._sheet_path = None
            self._rows = []
            self._items_by_id = {}
            self._table.setRowCount(0)
            self._status.setText(f"검토 자료를 불러오지 못했습니다. {error}")
            self._finalize.setEnabled(False)
            return
        self._queue = queue
        self._sheet_path = sheet
        self._rows = rows
        self._items_by_id = {str(item["review_item_id"]): item for item in queue.items}
        self._status.setText(
            f"대기열 {queue.manifest['dataset_id']} · 작업표 {sheet.name} · "
            "규칙 힌트는 정답이 아닙니다."
        )
        self._finalize.setEnabled(True)
        self._reload_table()
        self._refresh_event_options()
        if self._rows:
            self._table.setCurrentCell(0, 0)
        else:
            self._display_row(-1)

    def _latest_queue(self) -> tuple[Path, HistoricalNewsReviewQueue]:
        root = self._research_dir / "historical-news-review-queues"
        candidates: list[tuple[str, Path, HistoricalNewsReviewQueue]] = []
        for manifest_path in root.glob("*/manifest.json"):
            try:
                queue = load_historical_news_review_queue(manifest_path.parent)
            except ValueError:
                continue
            candidates.append((str(queue.manifest.get("created_at", "")), manifest_path.parent, queue))
        if not candidates:
            raise ValueError(f"불변 뉴스 검토 대기열이 없습니다: {root}")
        _, path, queue = max(candidates, key=lambda value: (value[0], str(value[1])))
        return path, queue

    def _working_sheet(self, queue: HistoricalNewsReviewQueue) -> Path:
        root = self._research_dir / "historical-news-review-work"
        existing: list[Path] = []
        for path in root.glob("*.csv"):
            try:
                rows = load_historical_news_review_sheet(queue, path)
            except ValueError:
                continue
            if rows:
                existing.append(path)
        if existing:
            return max(existing, key=lambda path: path.stat().st_mtime_ns)
        output = root / f"{queue.manifest['dataset_id']}-priority.csv"
        export_historical_news_review_sheet(queue, output)
        return output

    def _reload_table(self) -> None:
        self._loading = True
        try:
            self._table.setRowCount(len(self._rows))
            for row_index, row in enumerate(self._rows):
                item = self._items_by_id[row["review_item_id"]]
                article = item.get("article", {})
                decision = row["human_decision"]
                label = dict((value, label) for label, value in _DECISIONS).get(decision, decision)
                values = (
                    label,
                    str(article.get("published_at", "")),
                    row["stock_names"],
                    str(article.get("title", "")),
                    str(item.get("priority", {}).get("maximum_rule_relevance_score", 0)),
                )
                for column, value in enumerate(values):
                    self._table.setItem(row_index, column, QTableWidgetItem(value))
        finally:
            self._loading = False
        self._update_progress()

    def _selection_changed(self, current_row: int, _current_column: int, previous_row: int, _previous_column: int) -> None:
        if self._loading:
            return
        if self._dirty and previous_row >= 0 and previous_row != current_row:
            if not self._save_current(show_message=False, advance=False):
                self._loading = True
                self._table.setCurrentCell(previous_row, 0)
                self._loading = False
                return
        self._display_row(current_row)

    def _display_row(self, row_index: int) -> None:
        self._loading = True
        try:
            self._current_row = row_index
            if row_index < 0 or row_index >= len(self._rows):
                self._title.clear()
                self._summary.clear()
                self._source.clear()
                self._set_form_enabled(False)
                return
            self._set_form_enabled(True)
            row = self._rows[row_index]
            item = self._items_by_id[row["review_item_id"]]
            article = item.get("article", {})
            self._title.setText(str(article.get("title", "")))
            self._summary.setPlainText(str(article.get("search_summary", "")))
            self._source.setText(
                f"{row['published_at']} · {row['stock_names']} · "
                f"규칙 관련성 {row['rule_relevant_hint']} / 점수 {row['maximum_rule_relevance_score']}"
            )
            decision_index = self._decision.findData(row["human_decision"])
            self._decision.setCurrentIndex(max(0, decision_index))
            self._event.setCurrentText(row["canonical_event_id"])
            profile_name = row["theme_profile_name"]
            profile_index = self._profile.findText(profile_name)
            if profile_name and profile_index < 0:
                self._profile.addItem(profile_name)
                profile_index = self._profile.findText(profile_name)
            self._profile.setCurrentIndex(profile_index if profile_index >= 0 else 0)
            self._themes.setText(row["theme_names"])
            self._notes.setPlainText(row["notes"])
            if row["reviewer"]:
                self._reviewer.setText(row["reviewer"])
            elif not self._reviewer.text().strip():
                self._reviewer.setText(getpass.getuser())
            self._apply_decision_fields()
            self._dirty = False
        finally:
            self._loading = False

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (
            self._decision, self._event, self._profile, self._themes,
            self._notes, self._reviewer, self._save, self._open_article,
        ):
            widget.setEnabled(enabled)

    def _decision_changed(self) -> None:
        if self._loading:
            return
        decision = str(self._decision.currentData() or "")
        if decision == "relevant" and not self._event.currentText().strip() and self._current_row >= 0:
            row = self._rows[self._current_row]
            published = row["published_at"][:10].replace("-", "")
            suffix = row["review_item_id"].rsplit("-", 1)[-1][:10]
            self._event.setCurrentText(f"event-{published}-{suffix}")
        if decision in {"not_relevant", "uncertain", ""}:
            self._event.setCurrentText("")
            self._profile.setCurrentIndex(0)
            self._themes.clear()
        self._apply_decision_fields()
        self._mark_dirty()

    def _apply_decision_fields(self) -> None:
        relevant = str(self._decision.currentData() or "") == "relevant"
        self._event.setEnabled(relevant)
        self._profile.setEnabled(relevant)
        self._themes.setEnabled(relevant)

    def _mark_dirty(self, *_args) -> None:
        if not self._loading and self._current_row >= 0:
            self._dirty = True

    def _save_current(self, *, show_message: bool, advance: bool) -> bool:
        if self._queue is None or self._sheet_path is None or self._current_row < 0:
            return True
        row = self._rows[self._current_row]
        decision = str(self._decision.currentData() or "")
        values = {
            "human_decision": decision,
            "canonical_event_id": self._event.currentText().strip() if decision == "relevant" else "",
            "theme_profile_name": self._profile.currentText().strip() if decision == "relevant" else "",
            "theme_names": self._themes.text().strip() if decision == "relevant" else "",
            "notes": self._notes.toPlainText().strip(),
            "reviewer": self._reviewer.text().strip() if decision else "",
            "reviewed_at": datetime.now(_SEOUL).isoformat() if decision else "",
        }
        try:
            updated = update_historical_news_review_sheet_row(
                self._queue, self._sheet_path, row["review_item_id"], values,
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "검토 저장", str(error))
            return False
        self._rows[self._current_row] = updated
        self._dirty = False
        self._update_table_status(self._current_row)
        self._refresh_event_options()
        self._update_progress()
        if show_message:
            self._status.setText("현재 판정을 작업 CSV에 저장했습니다. 아직 불변 학습 정답은 아닙니다.")
        if advance:
            self._select_next_pending()
        return True

    def _clear_current(self) -> None:
        if self._current_row < 0:
            return
        self._loading = True
        self._decision.setCurrentIndex(0)
        self._event.setCurrentText("")
        self._profile.setCurrentIndex(0)
        self._themes.clear()
        self._notes.clear()
        self._loading = False
        self._dirty = True
        self._save_current(show_message=True, advance=False)
        self._display_row(self._current_row)

    def _update_table_status(self, row_index: int) -> None:
        decision = self._rows[row_index]["human_decision"]
        label = dict((value, label) for label, value in _DECISIONS).get(decision, decision)
        item = self._table.item(row_index, 0)
        if item is not None:
            item.setText(label)

    def _update_progress(self) -> None:
        completed = sum(bool(row.get("human_decision")) for row in self._rows)
        self._progress.setText(f"검토 {completed}/{len(self._rows)}")

    def _refresh_event_options(self) -> None:
        current = self._event.currentText()
        values = sorted({
            row["canonical_event_id"].strip()
            for row in self._rows if row.get("canonical_event_id", "").strip()
        })
        self._event.blockSignals(True)
        self._event.clear()
        self._event.addItems(values)
        self._event.setCurrentText(current)
        self._event.blockSignals(False)

    def _populate_profiles(self) -> None:
        self._profile.clear()
        self._profile.addItem("")
        if self._theme_repository is None:
            return
        try:
            profiles = tuple(self._theme_repository.list_profiles())
            active = str(getattr(self._theme_repository, "active_profile", ""))
        except (AttributeError, OSError, ValueError):
            return
        self._profile.addItems(profiles)
        if active in profiles:
            self._profile.setCurrentText(active)

    def _select_next_pending(self) -> None:
        if not self._rows:
            return
        start = max(self._current_row + 1, 0)
        order = list(range(start, len(self._rows))) + list(range(0, start))
        target = next((index for index in order if not self._rows[index]["human_decision"]), None)
        if target is None:
            self._status.setText("현재 작업표의 모든 기사를 검토했습니다.")
            return
        self._table.setCurrentCell(target, 0)
        self._table.scrollToItem(self._table.item(target, 0))

    def _open_current_article(self) -> None:
        if self._current_row < 0:
            return
        row = self._rows[self._current_row]
        article = self._items_by_id[row["review_item_id"]].get("article", {})
        url = str(article.get("original_url") or article.get("article_url") or "").strip()
        if not url:
            QMessageBox.information(self, "원문 열기", "저장된 원문 주소가 없습니다.")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _finalize_decisions(self) -> None:
        if self._queue is None or self._sheet_path is None:
            return
        if self._dirty and not self._save_current(show_message=False, advance=False):
            return
        try:
            dataset = build_historical_news_review_decisions(self._queue, self._sheet_path)
            output = (
                self._research_dir / "historical-news-review-decisions"
                / str(dataset.manifest["dataset_id"])
            )
            if output.exists():
                self._status.setText(f"같은 불변 검토 결과가 이미 있습니다: {output.name}")
                return
            write_historical_news_review_decisions(dataset, output)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "검토 결과 동결", str(error))
            return
        counts = dataset.manifest["counts"]
        QMessageBox.information(
            self,
            "검토 결과 동결",
            f"사람 판정 {counts['decisions']}건을 불변 결과로 저장했습니다.\n"
            f"관련 {counts['relevant']} · 무관 {counts['not_relevant']} · 보류 {counts['uncertain']}\n\n"
            "이 결과도 아직 모델 가중치 학습 준비 완료 상태는 아닙니다.",
        )
        self._status.setText(f"불변 검토 결과 저장 완료: {output.name}")

    def closeEvent(self, event) -> None:
        if self._dirty and not self._save_current(show_message=False, advance=False):
            event.ignore()
            return
        event.ignore()
        self.hide()
