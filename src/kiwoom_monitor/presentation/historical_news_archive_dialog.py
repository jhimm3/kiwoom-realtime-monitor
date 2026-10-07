"""Separate, read-only view of one sealed historical-news archive dataset."""

from __future__ import annotations

from html import escape
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout,
)

from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
from kiwoom_monitor.presentation.news_execution import dispose_finished_worker


class _ArchiveRequest(QThread):
    completed = Signal(int, str, str, object)
    failed = Signal(int, str, str)

    def __init__(self, client: CentralNewsClient, request_id: int, stock_code: str,
                 kind: str, args: dict[str, Any], parent: QDialog) -> None:
        super().__init__(parent)
        self._client = client
        self._request_id = request_id
        self._stock_code = stock_code
        self._kind = kind
        self._args = args

    def run(self) -> None:
        try:
            if self._kind == "page":
                if self._args.get("cursor") is None and not self._client.capabilities().get(
                    "historical_news_archive_v1", False
                ):
                    raise RuntimeError("과거 수집 자료 archive를 사용할 수 없습니다.")
                result = self._client.historical_archive_page(
                    self._stock_code, limit=200, cursor=self._args.get("cursor"),
                )
            else:
                result = self._client.historical_archive_article(
                    self._args["dataset_id"], self._args["article_revision_id"],
                    body_revision_id=self._args.get("body_revision_id"),
                )
        except (OSError, ValueError, RuntimeError) as error:
            self.failed.emit(self._request_id, self._stock_code, str(error))
            return
        self.completed.emit(self._request_id, self._stock_code, self._kind, result)


class HistoricalNewsArchiveDialog(QDialog):
    """Archive cursor, selection and request lifetime never enter the live-news UI."""

    def __init__(self, client: CentralNewsClient, parent: QDialog | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("과거 수집 자료 · 읽기 전용")
        self.resize(840, 600)
        self._client = client
        self._stock_code = ""
        self._stock_name = ""
        self._dataset_id: str | None = None
        self._cursor: str | None = None
        self._items: list[dict[str, Any]] = []
        self._request_id = 0
        self._worker: _ArchiveRequest | None = None
        self._pending: tuple[int, str, str, dict[str, Any]] | None = None

        self._status = QLabel("종목을 선택하세요.")
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(("발행 시각", "본문 상태", "제목"))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._request_selected_detail)
        self._detail = QTextBrowser()
        self._detail.setOpenExternalLinks(True)
        self._detail.setPlaceholderText("과거 기사를 선택하면 PC에서 확정한 본문과 판정 상태를 표시합니다.")
        self._more = QPushButton("다음 200건")
        self._more.setEnabled(False)
        self._more.clicked.connect(self.load_more)
        refresh = QPushButton("처음부터 다시 조회")
        refresh.clicked.connect(self.refresh)
        buttons = QHBoxLayout()
        buttons.addWidget(refresh)
        buttons.addStretch()
        buttons.addWidget(self._more)
        layout = QVBoxLayout(self)
        layout.addWidget(self._status)
        layout.addWidget(self._table, 3)
        layout.addWidget(self._detail, 2)
        layout.addLayout(buttons)

    def set_stock(self, stock_code: str, stock_name: str) -> None:
        if stock_code == self._stock_code:
            return
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._request_id += 1
        self._pending = None
        self._dataset_id = None
        self._cursor = None
        self._items.clear()
        self._table.setRowCount(0)
        self._detail.clear()
        self._more.setEnabled(False)
        self._status.setText(f"{stock_name} ({stock_code}) · 과거 수집 자료")
        if stock_code and self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        if not self._stock_code:
            return
        self._request_id += 1
        self._dataset_id = None
        self._cursor = None
        self._items.clear()
        self._table.setRowCount(0)
        self._detail.clear()
        self._more.setEnabled(False)
        self._queue("page", {"cursor": None})

    def load_if_needed(self) -> None:
        if self._dataset_id is None and self._pending is None and self._worker is None:
            self.refresh()

    def load_more(self) -> None:
        if self._cursor and self._dataset_id and self._worker is None:
            self._queue("page", {"cursor": self._cursor})

    def _queue(self, kind: str, args: dict[str, Any]) -> None:
        self._request_id += 1
        self._pending = (self._request_id, self._stock_code, kind, args)
        self._more.setEnabled(False)
        self._status.setText(f"{self._stock_name} · 과거 자료 조회 중…")
        self._start_pending()

    def _start_pending(self) -> None:
        if self._worker is not None or self._pending is None:
            return
        request_id, stock_code, kind, args = self._pending
        self._pending = None
        worker = _ArchiveRequest(self._client, request_id, stock_code, kind, args, self)
        self._worker = worker
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _on_completed(self, request_id: int, stock_code: str, kind: str,
                      result: object) -> None:
        if request_id != self._request_id or stock_code != self._stock_code or not isinstance(result, dict):
            return
        if kind == "detail":
            self._show_detail(result)
            self._more.setEnabled(bool(self._cursor))
            self._status.setText(f"{self._stock_name} · 과거 수집 자료 {len(self._items)}건")
            return
        dataset_id = result.get("dataset_id")
        if self._dataset_id is not None and dataset_id != self._dataset_id:
            self._cursor = None
            self._status.setText("archive 세대가 변경되었습니다. 처음부터 다시 조회하세요.")
            return
        self._dataset_id = dataset_id
        rows = result["items"]
        self._table.setUpdatesEnabled(False)
        try:
            for row in rows:
                display = row.get("display") or {}
                index = len(self._items)
                self._items.append(row)
                self._table.insertRow(index)
                for column, value in enumerate((display.get("published_at") or "시각 없음",
                                                row.get("body_status") or "미확인",
                                                display.get("title") or "")):
                    self._table.setItem(index, column, QTableWidgetItem(str(value)))
        finally:
            self._table.setUpdatesEnabled(True)
        self._cursor = result.get("next_cursor")
        self._more.setEnabled(bool(self._cursor))
        self._status.setText(f"{self._stock_name} · 과거 수집 자료 {len(self._items)}건"
                             + (" · 다음 페이지 있음" if self._cursor else ""))

    def _on_failed(self, request_id: int, stock_code: str, message: str) -> None:
        if request_id == self._request_id and stock_code == self._stock_code:
            self._more.setEnabled(bool(self._cursor))
            self._status.setText(f"과거 자료 조회 실패: {message}")

    def _on_finished(self) -> None:
        worker = self._worker
        self._worker = None
        dispose_finished_worker(worker)
        self._start_pending()

    def _request_selected_detail(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._items) or not self._dataset_id:
            return
        item = self._items[row]
        self._queue("detail", {"dataset_id": self._dataset_id,
                               "article_revision_id": item["article_revision_id"],
                               "body_revision_id": item.get("body_revision_id")})

    def _show_detail(self, article: dict[str, Any]) -> None:
        document = article.get("document") or {}
        body = article.get("body")
        body_status = body.get("status") if isinstance(body, dict) else "missing"
        body_text = body.get("text") if isinstance(body, dict) else None
        if body_status == "fulltext" and body_text:
            content = escape(str(body_text))
        elif body_status == "summary_only":
            content = "요약 자료만 확보됨"
        else:
            content = f"본문 상태: {escape(str(body_status))} · 확보된 본문 없음"
        assessment_status = escape(str(article.get("assessment_status") or "unassessed"))
        events = article.get("events") or []
        self._detail.setHtml(
            f"<h3>{escape(str(document.get('title') or '제목 없음'))}</h3>"
            f"<p>기사 ID: {escape(str(article['article_revision_id']))}<br>"
            f"본문 상태: {escape(str(body_status))}<br>"
            f"판정 상태: {assessment_status}<br>"
            f"연결 사건: {len(events)}건</p>"
            f"<p style='white-space:pre-wrap'>{content}</p>"
        )

    def shutdown(self) -> None:
        self._request_id += 1
        self._pending = None
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait()
        self.close()
