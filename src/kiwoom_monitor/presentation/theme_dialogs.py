"""테마 가져오기·편집·프로필 관리 대화상자."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from kiwoom_monitor.application.theme_matching import MatchedThemeRow, match_theme_rows
from kiwoom_monitor.application.theme_preview import preview_theme_changes
from kiwoom_monitor.domain.theme_import import validate_theme_rows
from kiwoom_monitor.domain.theme_parser import parse_themes, theme_key
from kiwoom_monitor.domain.theme_text_import import parse_theme_text
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.presentation.similar_stock_dialog import choose_similar_stock, confirm_pending_name_change


def _section_separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setFixedHeight(1)
    line.setStyleSheet("background-color: #B8B8B8; border: 0;")
    return line


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700; color: #1F4E79; padding-top: 3px;")
    return label


class ThemePreviewDialog(QDialog):
    def __init__(self, changes: tuple[object, ...], skipped: int, parent: QWidget | None = None, excluded_theme_keys: frozenset[str] = frozenset()) -> None:
        super().__init__(parent)
        self.setWindowTitle("테마 변경 미리보기")
        self.resize(840, 460)
        self.setMinimumSize(680, 360)
        self.setSizeGripEnabled(True)
        self._excluded_theme_keys = excluded_theme_keys
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"전체 {len(changes) + skipped} · 오류/제외 {skipped}\n기본은 테마 추가입니다. 각 행에서 추가 또는 변경을 고르고, 적용 테마 칸을 직접 수정할 수 있습니다."))
        table = QTableWidget(len(changes), 5)
        table.setHorizontalHeaderLabels(("종목", "기존 테마", "적용 방식", "적용 테마", "상태"))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((150, 190, 120, 270, 110)):
            table.setColumnWidth(column, width)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._changes = tuple(changes)
        for index, change in enumerate(changes):
            read_only_items = (
                (0, str(getattr(change, "name", ""))),
                (1, ", ".join(getattr(change, "before", ()))),
            )
            for column, text in read_only_items:
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(index, column, item)
            mode = QComboBox()
            mode.addItem("테마 추가", "append")
            mode.addItem("테마 변경", "replace")
            mode.currentIndexChanged.connect(lambda _, row=index: self._update_operation_themes(row))
            table.setCellWidget(index, 2, mode)
            applied = self._without_excluded(self._merged_themes(getattr(change, "before", ()), getattr(change, "after", ())))
            table.setItem(index, 3, QTableWidgetItem(", ".join(applied)))
            status = QTableWidgetItem(self._status_for(getattr(change, "before", ()), applied, "append"))
            status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(index, 4, status)
        self._table = table
        layout.addWidget(table)
        actions = QHBoxLayout()
        actions.addStretch()
        apply = QPushButton("적용")
        cancel = QPushButton("취소")
        apply.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        actions.addWidget(apply)
        actions.addWidget(cancel)
        layout.addLayout(actions)

    def changes(self, separators: str) -> tuple[object, ...]:
        edited: list[object] = []
        for index, change in enumerate(self._changes):
            item = self._table.item(index, 3)
            after = self._without_excluded(parse_themes(item.text() if item else "", separators))
            before = tuple(getattr(change, "before", ()))
            mode = self._table.cellWidget(index, 2)
            status = self._status_for(before, after, str(mode.currentData()) if mode is not None else "replace")
            edited.append(replace(change, after=after, status=status))
        return tuple(edited)

    def _update_operation_themes(self, row: int) -> None:
        change = self._changes[row]
        mode = self._table.cellWidget(row, 2)
        after = tuple(getattr(change, "after", ())) if mode is None or mode.currentData() == "replace" else self._merged_themes(getattr(change, "before", ()), getattr(change, "after", ()))
        after = self._without_excluded(after)
        item = self._table.item(row, 3)
        if item is not None:
            item.setText(", ".join(after))
        status = self._table.item(row, 4)
        if status is not None:
            status.setText(self._status_for(getattr(change, "before", ()), after, str(mode.currentData()) if mode is not None else "replace"))

    @staticmethod
    def _merged_themes(before: object, imported: object) -> tuple[str, ...]:
        values: list[str] = []
        for theme in tuple(before) + tuple(imported):
            if all(str(theme).casefold() != existing.casefold() for existing in values):
                values.append(str(theme))
        return tuple(values)

    def _without_excluded(self, themes: object) -> tuple[str, ...]:
        return tuple(str(theme) for theme in themes if theme_key(str(theme)) not in self._excluded_theme_keys)

    @staticmethod
    def _status_for(before: object, after: object, mode: str = "replace") -> str:
        before_values = tuple(str(value) for value in before)
        after_values = tuple(str(value) for value in after)
        if frozenset(value.casefold() for value in before_values) == frozenset(value.casefold() for value in after_values):
            return "변경 없음"
        return "신규" if not before_values else ("테마 추가" if mode == "append" else "테마 변경")


class ImageThemeRowsDialog(QDialog):
    def __init__(
        self,
        rows: tuple[object, ...],
        parent: QWidget | None = None,
        settings: SettingsRepository | None = None,
        settings_prefix: str = "theme_image_import",
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._settings_prefix = settings_prefix
        self.setWindowTitle("이미지 테마 OCR 수정")
        self.resize(680, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("OCR 결과를 수정하세요. 행 추가·삭제가 가능하며, Excel의 종목명·테마 두 열을 복사해 Ctrl+V로 한 번에 붙여넣을 수 있습니다."))
        self._validation_message = QLabel()
        self._validation_message.setStyleSheet("color: #C00000;")
        self._validation_message.setWordWrap(True)
        layout.addWidget(self._validation_message)
        if settings is not None:
            rules = QFormLayout()
            self._custom_separators = QLineEdit(settings.get(f"{settings_prefix}_custom_separators"))
            self._custom_separators.setPlaceholderText("기본 , / | ; 외에 추가할 구분 문자")
            self._import_exclusions = QLineEdit(settings.get(f"{settings_prefix}_exclusions"))
            self._import_exclusions.setPlaceholderText("예: 개별이슈, 공시")
            rules.addRow("추가 구분자", self._custom_separators)
            rules.addRow("제외 테마", self._import_exclusions)
            layout.addLayout(rules)
        self.table = QTableWidget(len(rows), 2)
        self.table.setHorizontalHeaderLabels(("종목명", "테마"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        for index, row in enumerate(rows):
            name = row[0] if isinstance(row, tuple) and len(row) >= 1 else getattr(row, "name", "")
            themes = row[1] if isinstance(row, tuple) and len(row) >= 2 else getattr(row, "themes", "")
            self.table.setItem(index, 0, QTableWidgetItem(str(name)))
            self.table.setItem(index, 1, QTableWidgetItem(str(themes)))
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        add = QPushButton("행 추가")
        remove = QPushButton("선택 행 삭제")
        add.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()) if self.table.currentRow() >= 0 else None)
        actions.addWidget(add); actions.addWidget(remove); actions.addStretch()
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_valid); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_if_valid(self) -> None:
        seen: dict[str, int] = {}
        duplicates: list[tuple[int, int, str]] = []
        default_background = self.table.palette().brush(QPalette.ColorRole.Base)
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            theme_item = self.table.item(row, 1)
            for item in (name_item, theme_item):
                if item is not None:
                    item.setBackground(default_background)
            name = name_item.text().strip() if name_item is not None else ""
            themes = theme_item.text().strip() if theme_item is not None else ""
            if not name or not themes:
                continue
            key = "".join(name.split()).casefold()
            if key in seen:
                duplicates.append((seen[key], row, name))
            else:
                seen[key] = row
        if not duplicates:
            self._validation_message.clear()
            if self._settings is not None:
                self._settings.set(f"{self._settings_prefix}_custom_separators", self._custom_separators.text().strip())
                self._settings.set(f"{self._settings_prefix}_exclusions", self._import_exclusions.text().strip())
            self.accept()
            return
        for first_row, duplicate_row, _ in duplicates:
            for row in (first_row, duplicate_row):
                for column in range(2):
                    item = self.table.item(row, column)
                    if item is not None:
                        item.setBackground(QColor("#FCE4D6"))
        first_row, duplicate_row, name = duplicates[0]
        self._validation_message.setText(
            f"{duplicate_row + 1}행의 종목명 '{name}'이(가) {first_row + 1}행과 중복됩니다. "
            "한 행으로 합치거나 종목명을 수정한 뒤 다시 미리보기를 누르세요."
        )
        self.table.setCurrentCell(duplicate_row, 0)
        self.table.scrollToItem(self.table.item(duplicate_row, 0))

    def eventFilter(self, watched: object, event: object) -> bool:
        if watched in (self.table, self.table.viewport()) and getattr(event, "type", lambda: None)() == QEvent.Type.KeyPress:
            if getattr(event, "matches", lambda _: False)(QKeySequence.StandardKey.Paste):
                self._paste_rows_from_clipboard()
                return True
        return super().eventFilter(watched, event)  # type: ignore[arg-type]

    def _paste_rows_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            return
        start_row = max(0, self.table.currentRow())
        for offset, line in enumerate(text.splitlines()):
            values = [value.strip() for value in line.split("\t")]
            if not any(values):
                continue
            row = start_row + offset
            while row >= self.table.rowCount():
                self.table.insertRow(self.table.rowCount())
            self.table.setItem(row, 0, QTableWidgetItem(values[0] if values else ""))
            self.table.setItem(row, 1, QTableWidgetItem(values[1] if len(values) > 1 else ""))
        self.table.setCurrentCell(start_row, 0)

    def rows(self) -> tuple[tuple[str, str], ...]:
        values = []
        for index in range(self.table.rowCount()):
            name = self.table.item(index, 0)
            themes = self.table.item(index, 1)
            if name and name.text().strip() and themes and themes.text().strip():
                values.append((name.text().strip(), themes.text().strip()))
        return tuple(values)


class ImageThemeImportOptionsDialog(QDialog):
    """Keep image-layout choices close to the action that uses them."""

    def __init__(self, settings: SettingsRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("이미지 테마 업데이트")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("이미지의 테마가 표시된 방식을 선택한 뒤 이미지를 고르세요."))
        form = QFormLayout()
        self._mode = QComboBox()
        self._mode.addItem("테마 열 읽기", "theme_column")
        self._mode.addItem("색상 배지 읽기", "reason_badges")
        self._mode.addItem("테마 열 + 색상 배지 함께 읽기", "both")
        saved_mode = settings.get("theme_image_import_mode")
        self._mode.setCurrentIndex({"theme_column": 0, "reason_badges": 1, "both": 2}.get(saved_mode, 0))
        self._theme_header = QLineEdit(settings.get("theme_image_import_theme_header"))
        self._theme_header.setPlaceholderText("예: 테마, 관련 테마")
        form.addRow("이미지 테마 읽기 방식", self._mode)
        form.addRow("테마 열 제목", self._theme_header)
        layout.addLayout(form)
        layout.addWidget(QLabel("색상 배지만 읽는 방식에서는 테마 열 제목을 사용하지 않습니다."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("이미지 선택")
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def mode(self) -> str:
        return str(self._mode.currentData())

    def _save_and_accept(self) -> None:
        self._settings.set("theme_image_import_mode", self.mode)
        self._settings.set("theme_image_import_theme_header", self._theme_header.text().strip())
        self.accept()


class TextThemeImportDialog(QDialog):
    def __init__(self, settings: SettingsRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("텍스트 테마 업데이트")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("테마/종목 목록을 그대로 붙여넣으세요. 🔥 뒤 제목을 대분류 테마로 적용합니다."))
        rules = QFormLayout()
        self._heading_marker = QLineEdit(settings.get("theme_text_heading_marker"))
        self._custom_separators = QLineEdit(settings.get("theme_text_import_custom_separators"))
        self._custom_separators.setPlaceholderText("기본 , / | ; 외에 추가할 구분 문자")
        self._import_exclusions = QLineEdit(settings.get("theme_text_import_exclusions"))
        self._import_exclusions.setPlaceholderText("예: 개별이슈, 공시")
        self._heading_marker.setPlaceholderText("기본: 🔥 · 예: ⭐ 또는 테마:")
        rules.addRow("텍스트 테마 시작 표시", self._heading_marker)
        rules.addRow("추가 구분자", self._custom_separators)
        rules.addRow("제외 테마", self._import_exclusions)
        layout.addLayout(rules)
        self._include_subcategories = QCheckBox("# 소분류도 별도 테마로 추가")
        self._include_subcategories.setChecked(settings.get("theme_text_include_subcategories") == "1")
        layout.addWidget(self._include_subcategories)
        layout.addWidget(QLabel("선택하면 #mRNA 같은 소분류 종목에 대분류와 mRNA 테마를 함께 적용합니다."))
        self._text = QTextEdit()
        self._update_placeholder()
        self._heading_marker.textChanged.connect(self._save_heading_marker)
        layout.addWidget(self._text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("미리보기")
        example_button = buttons.addButton("예시 넣기", QDialogButtonBox.ButtonRole.ActionRole)
        example_button.clicked.connect(self._insert_example)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save_heading_marker(self, value: str) -> None:
        self._settings.set("theme_text_heading_marker", value.strip() or "🔥")
        self._update_placeholder()

    def _save_and_accept(self) -> None:
        self._save_heading_marker(self._heading_marker.text())
        self._settings.set("theme_text_import_custom_separators", self._custom_separators.text().strip())
        self._settings.set("theme_text_import_exclusions", self._import_exclusions.text().strip())
        self._settings.set("theme_text_include_subcategories", "1" if self._include_subcategories.isChecked() else "0")
        self.accept()

    def _update_placeholder(self) -> None:
        marker = self._heading_marker.text().strip() or "🔥"
        self._text.setPlaceholderText(
            "예:\n"
            f"{marker}반도체\n"
            "삼성전자, SK하이닉스, 네패스\n\n"
            f"{marker}바이오\n"
            "삼양바이오팜, 나이벡, 에스티팜\n\n"
            f"{marker}스테이블코인\n"
            "SK증권, 다날, 카카오페이"
        )

    def _insert_example(self) -> None:
        marker = self._heading_marker.text().strip() or "🔥"
        self._text.setPlainText(
            f"{marker}반도체\n"
            "삼성전자, SK하이닉스, 네패스\n\n"
            f"{marker}바이오\n"
            "삼양바이오팜, 나이벡, 에스티팜\n\n"
            f"{marker}스테이블코인\n"
            "SK증권, 다날, 카카오페이"
        )

    @property
    def text(self) -> str:
        return self._text.toPlainText()

    @property
    def include_subcategories(self) -> bool:
        return self._include_subcategories.isChecked()


class ThemeEditDialog(QDialog):
    """테마를 배지 형태로 바로 추가·삭제하는 편집창."""

    def __init__(self, stock_name: str, themes: tuple[str, ...], separators: str = ",/|;", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("테마 편집")
        self.resize(520, 230)
        self.setMinimumSize(380, 180)
        self._themes = list(themes)
        self._separators = separators
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{stock_name}의 테마"))
        self._badges = QHBoxLayout()
        self._badges.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._badges)
        entry = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("추가할 테마를 입력하세요 (설정한 구분자 사용 가능)")
        add = QPushButton("추가")
        add.clicked.connect(self._add_themes)
        self._input.returnPressed.connect(self._add_themes)
        entry.addWidget(self._input)
        entry.addWidget(add)
        layout.addLayout(entry)
        edit = QPushButton("기존 테마 수정…")
        edit.setToolTip("기존 테마 이름을 바꿉니다. 새 테마는 위 입력칸에서 추가하세요.")
        edit.clicked.connect(self._edit_theme)
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._render_badges()

    @property
    def themes(self) -> tuple[str, ...]:
        return tuple(self._themes)

    def _add_themes(self) -> None:
        for theme in parse_themes(self._input.text(), self._separators):
            if theme not in self._themes:
                self._themes.append(theme)
        self._input.clear()
        self._render_badges()

    def _remove_theme(self, theme: str) -> None:
        self._themes.remove(theme)
        self._render_badges()

    def _edit_theme(self) -> None:
        if not self._themes:
            return
        before, ok = QInputDialog.getItem(self, "기존 테마 수정", "수정할 테마", self._themes, 0, False)
        if not ok:
            return
        after, ok = QInputDialog.getText(self, "기존 테마 수정", "새 테마 이름", text=before)
        if not ok or not after.strip():
            return
        replacement = parse_themes(after, self._separators)
        if len(replacement) != 1:
            QMessageBox.warning(self, "입력 확인", "수정할 테마는 하나만 입력하세요.")
            return
        value = replacement[0]
        index = self._themes.index(before)
        if value.casefold() != before.casefold() and any(theme.casefold() == value.casefold() for theme in self._themes):
            QMessageBox.warning(self, "입력 확인", "이미 있는 테마입니다.")
            return
        self._themes[index] = value
        self._render_badges()

    def _render_badges(self) -> None:
        while self._badges.count():
            item = self._badges.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for theme in self._themes:
            badge = QPushButton(f"{theme}  ×")
            badge.setToolTip("클릭하면 이 테마를 삭제합니다")
            badge.clicked.connect(lambda _, value=theme: self._remove_theme(value))
            self._badges.addWidget(badge)
        self._badges.addStretch()


class ThemeColorDialog(QDialog):
    PALETTE = (
        "#DCE6F1", "#FFF2CC", "#E2F0D9", "#FCE4D6", "#E4DFEC",
        "#F4CCCC", "#D9EAD3", "#CFE2F3", "#D9D2E9", "#FCE5CD",
        "#B4C7E7", "#FFD966", "#A9D18E", "#F4B183", "#C9B1D4",
        "#EA9999", "#93C47D", "#9FC5E8", "#B4A7D6", "#F6B26B",
    )

    def __init__(self, theme: str, current: str, parent: QWidget | None = None, *, allow_stock_only: bool = True) -> None:
        super().__init__(parent); self.setWindowTitle("테마 색상 변경"); self._color = current
        layout=QVBoxLayout(self); layout.addWidget(QLabel(f"테마: {theme}"))
        self._stock_only=QRadioButton("이 종목만"); self._all_stocks=QRadioButton("이 테마 전체"); self._all_stocks.setChecked(True)
        if allow_stock_only:
            layout.addWidget(self._stock_only)
        layout.addWidget(self._all_stocks)
        colors=QGridLayout(); layout.addLayout(colors)
        for index, color in enumerate(self.PALETTE):
            button=QPushButton(); button.setFixedSize(28,28); button.setStyleSheet(f"background:{color}; border: 1px solid #888;")
            button.setToolTip(color)
            button.clicked.connect(lambda _, value=color: self._choose(value)); colors.addWidget(button, index // 5, index % 5)
        custom = QPushButton("전체 색상 선택…")
        custom.clicked.connect(self._choose_custom_color)
        layout.addWidget(custom)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _choose(self, color: str) -> None: self._color=color
    def _choose_custom_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "테마 색상 선택")
        if color.isValid():
            self._color = color.name()
    @property
    def color(self) -> str: return self._color
    @property
    def stock_only(self) -> bool: return self._stock_only.isChecked()


class ThemeBulkDeleteDialog(QDialog):
    def __init__(self, themes: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("테마 일괄 삭제")
        self.resize(380, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("삭제할 테마를 체크하세요. 선택한 테마는 모든 종목에서 제거됩니다."))
        self._list = QListWidget()
        for theme in themes:
            item = QListWidgetItem(theme)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._list.addItem(item)
        layout.addWidget(self._list)
        select_all = QPushButton("전체 선택 / 해제")
        select_all.clicked.connect(self._toggle_all)
        layout.addWidget(select_all)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("선택 테마 삭제")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def themes(self) -> tuple[str, ...]:
        return tuple(self._list.item(index).text() for index in range(self._list.count()) if self._list.item(index).checkState() == Qt.CheckState.Checked)

    def _toggle_all(self) -> None:
        checked = any(self._list.item(index).checkState() == Qt.CheckState.Checked for index in range(self._list.count()))
        state = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
        for index in range(self._list.count()):
            self._list.item(index).setCheckState(state)


class ThemeBulkEditDialog(QDialog):
    def __init__(self, themes: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("테마 일괄 수정 미리보기")
        self.resize(600, 500)
        layout = QVBoxLayout(self)
        guide = QLabel(
            "새 테마명에 쉼표(,), 슬래시(/), 세로줄(|), 세미콜론(;)을 넣으면 여러 테마로 나뉩니다.\n"
            "예: 해운이란 → 해운/이란   ·   같은 이름은 합쳐지고, 비우면 모든 종목에서 삭제됩니다."
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)
        self._table = QTableWidget(len(themes), 2)
        self._table.setHorizontalHeaderLabels(("기존 테마", "새 테마명"))
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, theme in enumerate(themes):
            before = QTableWidgetItem(theme)
            before.setFlags(before.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, before)
            self._table.setItem(row, 1, QTableWidgetItem(theme))
        layout.addWidget(self._table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("변경 적용")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def changes(self) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = []
        for row in range(self._table.rowCount()):
            before = self._table.item(row, 0).text().strip()
            after = self._table.item(row, 1).text().strip()
            if before.casefold() != after.casefold():
                values.append((before, after))
        return tuple(values)

class ThemeManagerDialog(QDialog):
    def __init__(self, repository: object, settings: SettingsRepository, on_excel_update: Callable[[], None] | None = None, on_image_update: Callable[[str], None] | None = None, on_catalog_sync: Callable[[], None] | None = None, parent: QWidget | None = None, on_themes_changed: Callable[[], None] | None = None) -> None:
        super().__init__(parent); self._repository=repository; self._settings=settings; self._separators=",/|;" + settings.get("theme_custom_separators"); self._on_excel_update=on_excel_update; self._on_image_update=on_image_update; self._on_themes_changed=on_themes_changed; self.setWindowTitle("종목/테마 관리"); self.resize(560,420)
        self._search=QLineEdit(); self._search.setPlaceholderText("종목명 검색"); self._table=QTableWidget(0,2); self._table.setHorizontalHeaderLabels(("종목명","테마"))
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, int(settings.get("theme_manager_stock_column_width")))
        self._table.setColumnWidth(1, int(settings.get("theme_manager_theme_column_width")))
        header.sectionResized.connect(self._save_table_column_width)
        self._add=QPushButton("신규 종목 테마 추가"); self._text_import = QPushButton("텍스트 테마 업데이트")
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        stock_tab = QWidget(); stock_layout = QVBoxLayout(stock_tab)
        theme_tab = QWidget(); theme_layout = QVBoxLayout(theme_tab)
        stock_layout.addWidget(_section_title("등록 종목/테마")); stock_layout.addWidget(self._search); stock_layout.addWidget(self._table)
        if on_catalog_sync is not None:
            last = settings.get("krx_stock_catalog_date")
            sync = QPushButton("상장종목 목록 동기화")
            sync.clicked.connect(on_catalog_sync)
            stock_layout.addWidget(_section_separator())
            stock_layout.addWidget(_section_title("상장목록 동기화"))
            stock_layout.addWidget(sync)
            stock_layout.addWidget(QLabel(f"마지막 동기화: {last or '없음'}"))
        stock_layout.addStretch()
        self._add.clicked.connect(self._add_new); self._text_import.clicked.connect(self._import_text)
        profile_row = QHBoxLayout()
        self._theme_profile = QComboBox()
        self._new_theme_profile = QPushButton("새 프로필")
        self._copy_theme_profile = QPushButton("복제")
        self._rename_theme_profile = QPushButton("이름 변경")
        self._delete_theme_profile = QPushButton("삭제")
        profile_row.addWidget(self._theme_profile, 1)
        profile_row.addWidget(self._new_theme_profile)
        profile_row.addWidget(self._copy_theme_profile)
        profile_row.addWidget(self._rename_theme_profile)
        profile_row.addWidget(self._delete_theme_profile)
        theme_layout.addWidget(_section_title("테마 프로필"))
        theme_layout.addWidget(QLabel("프로필마다 종목 테마와 테마 색상을 별도로 관리합니다."))
        theme_layout.addLayout(profile_row)
        theme_layout.addWidget(_section_separator())
        theme_layout.addWidget(_section_title("테마 입력 및 갱신"))
        theme_layout.addWidget(self._add)
        theme_layout.addWidget(self._text_import)
        if self._on_excel_update is not None:
            excel = QPushButton("Excel 테마 업데이트")
            excel.clicked.connect(self._on_excel_update)
            theme_layout.addWidget(excel)
        if self._on_image_update is not None:
            image = QPushButton("이미지 테마 업데이트")
            image.clicked.connect(self._start_image_update)
            theme_layout.addWidget(image)
        theme_layout.addWidget(_section_separator())
        theme_layout.addWidget(_section_title("테마 일괄 관리"))
        self._bulk_edit = QPushButton("테마 일괄 수정")
        self._clear_all = QPushButton("전체 테마 초기화")
        theme_layout.addWidget(self._bulk_edit)
        theme_layout.addWidget(self._clear_all)
        theme_layout.addStretch()
        tabs.addTab(theme_tab, "테마")
        tabs.addTab(stock_tab, "종목")
        layout.addWidget(tabs)
        self._bulk_edit.clicked.connect(self._rename_theme)
        self._clear_all.clicked.connect(self._clear_all_themes)
        self._theme_profile.currentTextChanged.connect(self._select_theme_profile)
        self._new_theme_profile.clicked.connect(lambda: self._create_theme_profile(copy_current=False))
        self._copy_theme_profile.clicked.connect(lambda: self._create_theme_profile(copy_current=True))
        self._rename_theme_profile.clicked.connect(self._rename_current_theme_profile)
        self._delete_theme_profile.clicked.connect(self._delete_current_theme_profile)
        self._search.textChanged.connect(self._reload); self._table.cellDoubleClicked.connect(self._edit); self._rows=()
        self._refresh_theme_profiles(); self._reload()
    def _refresh_theme_profiles(self) -> None:
        profiles = self._repository.list_profiles()
        active = getattr(self._repository, "active_profile", self._settings.get("theme_active_profile"))
        self._theme_profile.blockSignals(True)
        self._theme_profile.clear()
        self._theme_profile.addItems(profiles)
        self._theme_profile.setCurrentText(active if active in profiles else (profiles[0] if profiles else "기본 테마"))
        self._theme_profile.blockSignals(False)
    def _select_theme_profile(self, name: str) -> None:
        if not name:
            return
        self._repository.select_profile(name)
        self._settings.set("theme_active_profile", name)
        self._reload()
        self._notify_themes_changed()
    def _create_theme_profile(self, copy_current: bool) -> None:
        title = "현재 테마 복제" if copy_current else "새 테마 프로필"
        prompt = "새 프로필 이름을 입력하세요." if not copy_current else "현재 테마를 복제할 새 프로필 이름을 입력하세요."
        name, accepted = QInputDialog.getText(self, title, prompt)
        if not accepted:
            return
        try:
            created = self._repository.create_profile(name, copy_current=copy_current)
        except ValueError as error:
            QMessageBox.warning(self, title, str(error))
            return
        self._refresh_theme_profiles()
        self._theme_profile.setCurrentText(created)
    def _delete_current_theme_profile(self) -> None:
        name = self._theme_profile.currentText()
        if QMessageBox.question(self, "테마 프로필 삭제", f"'{name}' 프로필의 모든 테마를 삭제합니다.\n되돌릴 수 없습니다.\n\n계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete_profile(name)
        except ValueError as error:
            QMessageBox.warning(self, "테마 프로필 삭제", str(error))
            return
        self._refresh_theme_profiles()
        self._select_theme_profile(self._theme_profile.currentText())
    def _rename_current_theme_profile(self) -> None:
        before = self._theme_profile.currentText()
        after, accepted = QInputDialog.getText(self, "테마 프로필 이름 변경", "새 프로필 이름을 입력하세요.", text=before)
        if not accepted:
            return
        try:
            renamed = self._repository.rename_profile(before, after)
        except ValueError as error:
            QMessageBox.warning(self, "테마 프로필 이름 변경", str(error))
            return
        self._settings.set("theme_active_profile", renamed)
        self._refresh_theme_profiles()
        self._theme_profile.setCurrentText(renamed)
    def _start_image_update(self) -> None:
        if self._on_image_update is None:
            return
        dialog = ImageThemeImportOptionsDialog(self._settings, self)
        if dialog.exec():
            self._on_image_update(dialog.mode)
    def _save_table_column_width(self, logical_index: int, _old_width: int, new_width: int) -> None:
        if logical_index == 0:
            self._settings.set("theme_manager_stock_column_width", str(new_width))
        elif logical_index == 1:
            self._settings.set("theme_manager_theme_column_width", str(new_width))
    def _filter_import_exclusions(
        self,
        rows: tuple[tuple[str, str], ...],
        separators: str,
        setting_key: str,
    ) -> tuple[tuple[str, str], ...]:
        excluded = {theme_key(theme) for theme in parse_themes(self._settings.get(setting_key), separators)}
        if not excluded:
            return rows
        filtered: list[tuple[str, str]] = []
        for name, value in rows:
            themes = tuple(theme for theme in parse_themes(value, separators) if theme_key(theme) not in excluded)
            if themes:
                filtered.append((name, "/".join(themes)))
        return tuple(filtered)
    def _reload(self) -> None:
        self._rows=self._repository.search(self._search.text()); self._table.setRowCount(len(self._rows))
        for index,(_,name,themes) in enumerate(self._rows):
            self._table.setItem(index,0,QTableWidgetItem(name)); self._table.setItem(index,1,QTableWidgetItem(themes))
    def _edit(self, row: int, _: int) -> None:
        code, name, themes = self._rows[row]
        dialog = ThemeEditDialog(name, parse_themes(themes, self._separators), self._separators, self)
        if dialog.exec() and QMessageBox.question(self, "테마 변경 확인", f"{name}\n\n기존: {themes or '-'}\n변경: {', '.join(dialog.themes) or '-'}\n\n저장할까요?") == QMessageBox.StandardButton.Yes:
            self._repository.replace_for_stock(code, dialog.themes)
            self._reload()
            self._notify_themes_changed()
    def _add_new(self) -> None:
        dialog = ImageThemeRowsDialog((), self, self._settings, "theme_new_import")
        dialog.setWindowTitle("신규 종목 테마 추가")
        labels = dialog.findChildren(QLabel)
        if labels:
            labels[0].setText("종목명과 테마를 여러 행으로 입력하세요. 행 추가·삭제가 가능하며, 적용 전 기존 테마와 비교합니다.")
        if not dialog.exec():
            return
        separators = ",/|;" + self._settings.get("theme_new_import_custom_separators")
        self._apply_import_rows(dialog.rows(), separators, "theme_new_import_exclusions")
        return

    def _import_text(self) -> None:
        dialog = TextThemeImportDialog(self._settings, self)
        if not dialog.exec():
            return
        separators = ",/|;" + self._settings.get("theme_text_import_custom_separators")
        marker = self._settings.get("theme_text_heading_marker")
        rows = parse_theme_text(dialog.text, separators, marker, dialog.include_subcategories)
        if not rows:
            QMessageBox.information(self, "텍스트 확인", f"{marker} 테마 제목 아래에서 종목명을 찾지 못했습니다. 텍스트 내용을 확인하세요.")
            return
        self._apply_import_rows(rows, separators, "theme_text_import_exclusions")

    def _apply_import_rows(self, raw_rows: tuple[tuple[str, str], ...], separators: str, exclusion_setting_key: str) -> None:
        valid, errors = validate_theme_rows(
            self._filter_import_exclusions(raw_rows, separators, exclusion_setting_key),
            separators,
        )
        if not valid and not errors:
            QMessageBox.information(self, "입력 확인", "추가할 종목과 테마를 한 행 이상 입력하세요.")
            return
        if errors:
            QMessageBox.warning(self, "입력 확인", "\n".join(errors))
            return
        matched, unmatched = match_theme_rows(valid, self._repository)
        resolved, cancelled = self._resolve_unmatched_new_rows(unmatched)
        if cancelled:
            return
        skipped = len(unmatched) - len(resolved)
        matched = matched + resolved
        unmatched = ()
        if unmatched:
            names = ", ".join(row.name for row in unmatched)
            QMessageBox.warning(self, "종목 매칭 실패", f"종목 DB에서 찾을 수 없습니다.\n{names}")
            return
        changes = preview_theme_changes(matched, self._repository)
        preview = ThemePreviewDialog(changes, skipped, self, frozenset(theme_key(theme) for theme in parse_themes(self._settings.get(exclusion_setting_key), separators)))
        if preview.exec():
            changes = preview.changes(separators)
            applied = sum(change.status != "변경 없음" for change in changes)
            for change in changes:
                if change.status != "변경 없음":
                    self._repository.replace_for_stock(change.code, change.after)
            self._reload()
            self._notify_themes_changed()
            QMessageBox.information(self, "테마 업데이트 완료", f"{applied}개 종목의 테마를 적용했습니다.")

    def _resolve_unmatched_new_rows(self, rows: tuple[object, ...]) -> tuple[tuple[MatchedThemeRow, ...], bool]:
        resolved: list[MatchedThemeRow] = []
        for row in rows:
            original_name = str(getattr(row, "name", ""))
            themes = tuple(getattr(row, "themes", ()))
            renamed, handled = confirm_pending_name_change(self, self._repository, original_name, themes)
            if handled:
                if renamed is not None:
                    code, current_name = renamed
                    resolved.append(MatchedThemeRow(code, current_name, themes))
                continue
            split_finder = getattr(self._repository, "find_concatenated_stocks", None)
            split = split_finder(original_name) if callable(split_finder) else ()
            if split:
                labels = " + ".join(name for _, name in split)
                if QMessageBox.question(
                    self,
                    "붙어 있는 종목명 확인",
                    f"'{original_name}'을(를) 다음 종목들로 나눌 수 있습니다.\n\n{labels}\n\n이대로 나눌까요?",
                ) == QMessageBox.StandardButton.Yes:
                    resolved.extend(MatchedThemeRow(code, name, themes) for code, name in split)
                    continue
            partial_finder = getattr(self._repository, "find_partial_concatenated_stocks", None)
            known, fragments = partial_finder(original_name) if callable(partial_finder) else ((), ())
            if known and fragments:
                known_labels = " + ".join(name for _, name in known)
                fragment_labels = ", ".join(fragments)
                if QMessageBox.question(
                    self,
                    "붙어 있는 종목명 일부 확인",
                    f"'{original_name}'에서 다음 종목은 확인됐습니다.\n\n{known_labels}\n\n"
                    f"남은 이름만 다시 찾습니다: {fragment_labels}\n\n계속할까요?",
                ) == QMessageBox.StandardButton.Yes:
                    resolved.extend(MatchedThemeRow(code, name, themes) for code, name in known)
                    for fragment in fragments:
                        candidate, cancelled = choose_similar_stock(self, self._repository, fragment, themes)
                        if cancelled:
                            return (), True
                        if candidate:
                            code, selected_name = candidate
                            resolved.append(MatchedThemeRow(code, selected_name, themes))
                    continue
            candidate, cancelled = choose_similar_stock(self, self._repository, original_name, themes)
            if candidate:
                code, selected_name = candidate
                resolved.append(MatchedThemeRow(code, selected_name, tuple(getattr(row, "themes", ()))))
            elif cancelled:
                return (), True
        return tuple(resolved), False

    def _edit_theme_color(self) -> None:
        options = self._repository.list_themes()
        if not options:
            QMessageBox.information(self, "테마 색상", "먼저 종목에 테마를 등록하세요.")
            return
        names = [name for name, _ in options]
        name, ok = QInputDialog.getItem(self, "테마 색상", "색상을 바꿀 테마", names, 0, False)
        if not ok:
            return
        dialog = ThemeColorDialog(name, dict(options)[name], self, allow_stock_only=False)
        if dialog.exec():
            self._repository.set_color(name, dialog.color)

    def _delete_themes(self) -> None:
        dialog = ThemeBulkDeleteDialog(tuple(name for name, _ in self._repository.list_themes()), self)
        if not dialog.exec() or not dialog.themes:
            return
        labels = ", ".join(dialog.themes)
        if QMessageBox.question(self, "테마 일괄 삭제", f"선택한 테마를 모든 종목에서 삭제합니다.\n\n{labels}\n\n계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        self._repository.delete_themes(dialog.themes)
        self._reload()
        self._notify_themes_changed()

    def _clear_all_themes(self) -> None:
        if QMessageBox.question(self, "전체 테마 초기화", "모든 종목의 테마와 테마 색상을 삭제합니다.\n이 작업은 되돌릴 수 없습니다.\n\n계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        self._repository.clear_all_themes()
        self._reload()
        self._notify_themes_changed()

    def _rename_theme(self) -> None:
        names = [name for name, _ in self._repository.list_themes()]
        if not names:
            QMessageBox.information(self, "테마 일괄 수정", "수정할 테마가 없습니다.")
            return
        dialog = ThemeBulkEditDialog(tuple(names), self)
        if not dialog.exec() or not dialog.changes:
            return
        parsed_changes = tuple((before, parse_themes(after, ",/|;")) for before, after in dialog.changes)
        preview = "\n".join(
            f"{before} → {' + '.join(targets) if targets else '삭제'}"
            for before, targets in parsed_changes
        )
        if QMessageBox.question(self, "테마 일괄 수정", f"다음 변경을 모든 종목에 적용합니다.\n\n{preview}\n\n계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        for before, targets in parsed_changes:
            try:
                if len(targets) > 1:
                    self._repository.split_theme(before, targets)
                elif targets:
                    self._repository.rename_theme(before, targets[0])
                else:
                    self._repository.delete_themes((before,))
            except ValueError as error:
                QMessageBox.warning(self, "테마 일괄 수정", str(error))
                return
        self._reload()
        self._notify_themes_changed()

    def _notify_themes_changed(self) -> None:
        if self._on_themes_changed is not None:
            self._on_themes_changed()
