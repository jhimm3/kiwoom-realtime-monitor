"""매매일지 표의 선택 표시 delegate."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget


class JournalCellMarkerDelegate(QStyledItemDelegate):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected_cell: tuple[int, int] | None = None
        self.marker_enabled = True

    def set_selected_cell(self, cell: tuple[int, int] | None) -> None:
        self._selected_cell = cell

    def paint(self, painter: QPainter, option: object, index: object) -> None:
        active = self.marker_enabled and (
            self._selected_cell == (index.row(), index.column())
            or bool(option.state & QStyle.StateFlag.State_MouseOver)
        )
        cell_option = QStyleOptionViewItem(option)
        cell_option.state &= ~(
            QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus
        )
        super().paint(painter, cell_option, index)
        if active:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#0078D7"))
            painter.drawRoundedRect(option.rect.left() + 1, option.rect.center().y() - 7, 2, 14, 1, 1)
            painter.restore()
