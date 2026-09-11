from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPolygon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.presentation.settings_dialog import SettingsDialog


class StockNameChangeReviewDialog(QDialog):
    """상호변경 후보를 사용자가 한 번 확인한 뒤 별칭으로 적용한다."""

    def __init__(self, changes: tuple[tuple[str, str, str, str], ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("종목명 변경 확인")
        self.resize(700, min(620, 190 + len(changes) * 34))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "같은 종목코드에서 이름이 변경된 항목입니다.\n"
            "체크한 항목만 과거 종목명을 현재 종목에 연결합니다. 체크를 해제하면 연결하지 않습니다."
        ))
        self._table = QTableWidget(len(changes), 5)
        self._table.setHorizontalHeaderLabels(("적용", "종목코드", "이전 이름", "현재 이름", "자료"))
        self._changes = changes
        for row, (code, old_name, new_name, source) in enumerate(changes):
            selected = QTableWidgetItem()
            selected.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selected.setCheckState(Qt.CheckState.Checked)
            self._table.setItem(row, 0, selected)
            for column, value in enumerate((code, old_name, new_name, source), start=1):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._table.setItem(row, column, item)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("확인 결과 저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("나중에 확인")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def decisions(self) -> dict[tuple[str, str], bool]:
        return {
            (code, old_name): self._table.item(row, 0).checkState() == Qt.CheckState.Checked
            for row, (code, old_name, _new_name, _source) in enumerate(self._changes)
        }


class ClickableLabel(QLabel):
    """클릭 동작을 지원하는 안내/요약 라벨."""

    def __init__(self, on_click: Callable[[], None], text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: object) -> None:
        if getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mouseReleaseEvent(event)  # type: ignore[arg-type]


class AlertSettingsDialog(QDialog):
    def __init__(self, settings: SettingsRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("알림 설정")
        self._enabled = QCheckBox("신고가 근접 강조 사용")
        self._enabled.setChecked(settings.get("near_high_alert_enabled") == "1")
        self._thresholds = {
            level: QLineEdit(settings.get(f"near_high_{level}_percent"))
            for level in ("interest", "caution", "fire")
        }
        layout = QFormLayout(self)
        layout.addRow(self._enabled)
        layout.addRow(
            "관심 / 주의 / 불(%)",
            SettingsDialog._strength_row(
                self._thresholds["interest"],
                self._thresholds["caution"],
                self._thresholds["fire"],
            ),
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _save(self) -> None:
        try:
            thresholds = tuple(float(self._thresholds[level].text()) for level in ("interest", "caution", "fire"))
        except ValueError:
            thresholds = (-1.0, -1.0, -1.0)
        if not (thresholds[0] >= thresholds[1] >= thresholds[2] >= 0):
            QMessageBox.warning(self, "입력 확인", "신고가 근접 기준은 관심 ≥ 주의 ≥ 불 순서의 0 이상 숫자로 입력하세요.")
            return
        self._settings.set("near_high_alert_enabled", "1" if self._enabled.isChecked() else "0")
        for level, value in zip(("interest", "caution", "fire"), thresholds):
            self._settings.set(f"near_high_{level}_percent", str(value))
        self.accept()


class NxtMarkerDelegate(QStyledItemDelegate):
    ACTIVE_MARKER_COLOR = QColor("#0078D7")

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._selected_cell: tuple[int, int] | None = None

    def set_selected_cell(self, cell: tuple[int, int] | None) -> None:
        self._selected_cell = cell

    def paint(self, painter: QPainter, option: object, index: object) -> None:
        clicked = self._selected_cell == (index.row(), index.column())
        active = clicked or bool(option.state & QStyle.StateFlag.State_MouseOver)
        cell_option = QStyleOptionViewItem(option)
        cell_option.state &= ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_HasFocus
        )
        super().paint(painter, cell_option, index)
        if active:
            rect = option.rect
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.ACTIVE_MARKER_COLOR)
            painter.drawRoundedRect(rect.left() + 1, rect.center().y() - 7, 2, 14, 1, 1)
            painter.restore()
        if not index.data(Qt.ItemDataRole.UserRole + 2):
            return
        rect = option.rect
        size = min(7, rect.width(), rect.height())
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#C00000"))
        painter.drawPolygon(QPolygon((
            QPoint(rect.right(), rect.bottom()),
            QPoint(rect.right() - size, rect.bottom()),
            QPoint(rect.right(), rect.bottom() - size),
        )))
        painter.restore()
