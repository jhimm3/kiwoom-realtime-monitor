"""메인 순위표의 열 표시와 순서를 편집하는 대화상자."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from kiwoom_monitor.infrastructure.persistence.column_settings_repository import (
    ColumnSetting,
    ColumnSettingsRepository,
)


class ColumnManagerDialog(QDialog):
    """기본 설정에서 여러 열의 표시 여부와 순서를 한 번에 편집한다."""

    def __init__(self, repository: ColumnSettingsRepository, columns: tuple[tuple[str, str], ...], table: QTableWidget, parent: QWidget | None = None, embedded: bool = False, on_applied: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self._repository = repository
        self._columns = columns
        self._table = table
        self._embedded = embedded
        self._on_applied = on_applied
        self._bulk_editing = False
        self.setWindowTitle("필드 편집")
        self.resize(360, 440)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("체크·전체 표시·순서 변경은 즉시 표에 반영됩니다. 항목을 위아래로 드래그해 순서를 바꿀 수 있습니다."))
        self._list = QListWidget()
        self._list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        header = table.horizontalHeader()
        for logical in sorted(range(len(columns)), key=header.visualIndex):
            _, label = columns[logical]
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, logical)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if not table.isColumnHidden(logical) else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        if embedded:
            self._list.itemChanged.connect(lambda _item: self._save_if_embedded())
            self._list.model().rowsMoved.connect(lambda *_args: self._save_if_embedded())
        layout.addWidget(self._list)

        tools = QHBoxLayout()
        show_all = QPushButton("전체 표시")
        show_all.clicked.connect(lambda: self._set_all_checked(True))
        hide_all = QPushButton("전체 숨김")
        hide_all.clicked.connect(lambda: self._set_all_checked(False))
        up = QPushButton("▲ 위로")
        up.clicked.connect(lambda: self._move_current(-1))
        down = QPushButton("▼ 아래로")
        down.clicked.connect(lambda: self._move_current(1))
        for button in (show_all, hide_all, up, down):
            tools.addWidget(button)
        layout.addLayout(tools)

        if not embedded:
            buttons = QDialogButtonBox()
            apply = buttons.addButton("적용", QDialogButtonBox.ButtonRole.ApplyRole)
            apply.clicked.connect(self._save)
            cancel = buttons.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
            cancel.clicked.connect(self.reject)
            layout.addWidget(buttons)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._bulk_editing = True
        for row in range(self._list.count()):
            self._list.item(row).setCheckState(state)
        self._bulk_editing = False
        self._save_if_embedded()

    def _move_current(self, offset: int) -> None:
        row = self._list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self._list.count():
            return
        item = self._list.takeItem(row)
        self._list.insertItem(target, item)
        self._list.setCurrentRow(target)
        self._save_if_embedded()

    def _save_if_embedded(self) -> None:
        if self._embedded and not self._bulk_editing:
            self._save()

    def _save(self) -> None:
        settings: list[ColumnSetting] = []
        for position in range(self._list.count()):
            item = self._list.item(position)
            logical = int(item.data(Qt.ItemDataRole.UserRole))
            name, _ = self._columns[logical]
            settings.append(ColumnSetting(name, item.checkState() == Qt.CheckState.Checked, position, self._table.columnWidth(logical)))
        if not any(setting.visible for setting in settings):
            QMessageBox.warning(self, "필드 편집", "최소 한 개의 열은 표시해야 합니다.")
            return
        self._repository.save(tuple(settings))
        if self._on_applied is not None:
            self._on_applied()
        if not self._embedded:
            self.accept()
