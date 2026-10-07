"""테마 가져오기에서 유사 종목과 종목명 변경을 확인하는 UI."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SimilarStockDialog(QDialog):
    def __init__(
        self,
        lookup: object,
        original_name: str,
        themes: tuple[str, ...] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._lookup = lookup
        self._original_name = original_name
        self.cancelled_all = False
        self.setWindowTitle("비슷한 종목 선택")
        self.resize(460, 390)
        layout = QVBoxLayout(self)
        theme_text = ", ".join(themes) if themes else "확인할 테마 없음"
        guide = QLabel(
            f"입력된 종목명: {original_name}\n"
            f"이 종목이 있던 테마: {theme_text}\n\n"
            "같은 종목명이 없습니다. 전체 상장종목에서 검색하거나 후보를 선택하세요."
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)
        self._search = QLineEdit(original_name)
        self._search.setPlaceholderText("종목명 검색")
        layout.addWidget(self._search)
        self._list = QListWidget()
        layout.addWidget(self._list)
        actions = QHBoxLayout()
        self._select = QPushButton("선택")
        skip = QPushButton("이번 종목 무시")
        cancel = QPushButton("전체 취소")
        actions.addStretch(); actions.addWidget(self._select); actions.addWidget(skip); actions.addWidget(cancel)
        layout.addLayout(actions)
        self._select.clicked.connect(self.accept)
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        skip.clicked.connect(self.reject)
        cancel.clicked.connect(self._cancel_all)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._reload)
        self._search.textChanged.connect(lambda: self._timer.start())
        self._reload()

    def _cancel_all(self) -> None:
        self.cancelled_all = True
        self.reject()

    def _reload(self) -> None:
        finder = getattr(self._lookup, "find_stock_candidates", None)
        rows = finder(self._search.text()) if callable(finder) else ()
        self._list.clear()
        for code, name in rows:
            item = QListWidgetItem(f"{name} ({code})")
            item.setData(Qt.ItemDataRole.UserRole, (code, name))
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._select.setEnabled(self._list.count() > 0)

    @property
    def selected(self) -> tuple[str, str] | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


def choose_similar_stock(
    parent: QWidget,
    lookup: object,
    name: str,
    themes: tuple[str, ...] = (),
) -> tuple[tuple[str, str] | None, bool]:
    dialog = SimilarStockDialog(lookup, name, themes, parent)
    if dialog.exec():
        return dialog.selected, False
    return None, dialog.cancelled_all


def confirm_pending_name_change(
    parent: QWidget,
    lookup: object,
    original_name: str,
    themes: tuple[str, ...] = (),
) -> tuple[tuple[str, str] | None, bool]:
    """확인 대기 중인 과거 종목명은 일반 유사 검색보다 먼저 안내한다."""
    finder = getattr(lookup, "pending_name_change", None)
    reviewer = getattr(lookup, "review_name_changes", None)
    change = finder(original_name) if callable(finder) else None
    if change is None or not callable(reviewer):
        return None, False
    code, old_name, new_name, source = change
    approved = QMessageBox.question(
        parent,
        "종목명 변경 확인",
        f"'{old_name}'은(는) 종목명이 변경된 것으로 확인됩니다.\n\n"
        f"이전 이름: {old_name}\n현재 이름: {new_name}\n종목코드: {code}\n자료: {source}\n"
        f"입력된 테마: {', '.join(themes) or '없음'}\n\n'{new_name}' 종목으로 적용할까요?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes
    reviewer({(code, old_name): approved})
    return ((code, new_name) if approved else None), True
