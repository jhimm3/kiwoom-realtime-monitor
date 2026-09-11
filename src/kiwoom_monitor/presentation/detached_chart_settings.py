"""차트 따로보기의 개별·전체 설정 대화상자."""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

class DetachedChartPanelSettingsDialog(QDialog):
    """한 차트에만 적용되는 배경·구분색 설정."""

    def __init__(self, settings: QSettings, panel_index: int, background_default: str,
                 accent_default: str, holding_default: str = "#FFEB96", parent: QWidget | None = None) -> None:
        super().__init__(parent); self._settings = settings; self._index = panel_index
        self._defaults = {"background": background_default, "accent": accent_default, "holding": holding_default,
                          "text_color": "#263238"}
        self.setWindowTitle(f"차트 {panel_index + 1} 설정")
        layout = QVBoxLayout(self); form = QFormLayout(); self.edits: dict[str, QLineEdit] = {}
        for key, label, default in (
            ("background", "차트 배경색", background_default),
            ("accent", "차트 구분색", accent_default),
            ("holding", "보유 구간 배경색", holding_default),
        ):
            edit = QLineEdit(settings.value(f"detached_chart_{panel_index}_{key}", default, type=str)); edit.setReadOnly(True)
            choose = QPushButton("색 선택")
            choose.clicked.connect(lambda _checked=False, target=edit, title=label: self._choose(target, title))
            row = QHBoxLayout(); row.addWidget(edit); row.addWidget(choose); form.addRow(label, row); self.edits[key] = edit
        text_edit = QLineEdit(settings.value(f"detached_chart_{panel_index}_text_color", "#263238", type=str)); text_edit.setReadOnly(True)
        text_choose = QPushButton("색 선택")
        text_choose.clicked.connect(lambda: self._choose(text_edit, "텍스트 색상"))
        text_row = QHBoxLayout(); text_row.addWidget(text_edit); text_row.addWidget(text_choose)
        form.addRow("텍스트 색상", text_row); self.edits["text_color"] = text_edit
        self.text_size = QSpinBox(); self.text_size.setRange(6, 72)
        self.text_size.setSuffix(" pt"); self.text_size.setValue(settings.value(f"detached_chart_{panel_index}_text_size", 12, type=int))
        form.addRow("텍스트 크기", self.text_size)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        reset = buttons.addButton("기본값", QDialogButtonBox.ButtonRole.ResetRole); reset.clicked.connect(self._reset)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("적용")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def save(self) -> None:
        for key, edit in self.edits.items():
            self._settings.setValue(f"detached_chart_{self._index}_{key}", edit.text())
        self._settings.setValue(f"detached_chart_{self._index}_text_size", self.text_size.value())

    def _choose(self, target: QLineEdit, title: str) -> None:
        color = QColorDialog.getColor(QColor(target.text()), self, title)
        if color.isValid(): target.setText(color.name())

    def _reset(self) -> None:
        for key, edit in self.edits.items(): edit.setText(self._defaults[key])
        self.text_size.setValue(12)



class DetachedChartSettingsDialog(QDialog):
    DEFAULTS = {
        "background": "#F7F8FA", "grid": "#D8DDE5", "foreground": "#374151",
        "up": "#D32F2F", "down": "#1976D2",
        "holding": "#FFEB96",
        "accent_0": "#3F6FA0", "accent_1": "#7A5BA7", "accent_2": "#3D8068",
        "accent_3": "#B06C3E", "accent_4": "#3B8793", "accent_5": "#8A6578",
    }

    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent); self._settings = settings; self.setWindowTitle("전용 차트 설정"); self.resize(500, 470)
        layout = QVBoxLayout(self); form_host = QWidget(); form = QFormLayout(form_host); self.colors: dict[str, QLineEdit] = {}
        labels = {
            "background": "전체 차트 배경색",
            "holding": "전체 보유 구간 배경색",
            "grid": "격자선", "foreground": "글자·축",
            "up": "상승봉·매수", "down": "하락봉·매도",
        }
        for key, label in labels.items():
            edit = QLineEdit(settings.value(f"detached_chart_color_{key}", self.DEFAULTS[key], type=str)); edit.setReadOnly(True)
            choose = QPushButton("색 선택"); choose.clicked.connect(lambda _checked=False, target=edit, title=label: self._choose(target, title))
            row = QHBoxLayout(); row.addWidget(edit); row.addWidget(choose); form.addRow(label, row); self.colors[key] = edit
        self.thresholds: dict[str, QDoubleSpinBox] = {}
        for key, label, maximum in (
            ("kospi_minute", "코스피 1분 거래대금 기준", 1_000_000.0),
            ("kospi_daily", "코스피 일봉 거래대금 기준", 10_000_000.0),
            ("kosdaq_minute", "코스닥 1분 거래대금 기준", 1_000_000.0),
            ("kosdaq_daily", "코스닥 일봉 거래대금 기준", 10_000_000.0),
        ):
            spin = QDoubleSpinBox(); spin.setRange(0, maximum); spin.setDecimals(1); spin.setSuffix(" 억"); spin.setSpecialValueText("사용 안 함")
            spin.setValue(float(settings.value(f"detached_{key}_trade_value", 0.0))); form.addRow(label, spin); self.thresholds[key] = spin
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(form_host); layout.addWidget(scroll)
        note = QLabel("전체 배경색·보유 구간색을 적용하면 차트 1~6에 일괄 저장됩니다. 이후 각 차트의 그림 초기화 옆 ⚙에서 개별 조정할 수 있습니다.")
        note.setWordWrap(True); note.setStyleSheet("color:#666666;"); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        reset = buttons.addButton("기본값", QDialogButtonBox.ButtonRole.ResetRole); reset.clicked.connect(self._reset)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("적용"); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self) -> None:
        for key, edit in self.colors.items(): self._settings.setValue(f"detached_chart_color_{key}", edit.text())
        # 전체 색상은 현재 6개 차트의 개별값에도 적용한다. 이후 각 차트 ⚙에서 다시 개별 조정할 수 있다.
        for index in range(6):
            self._settings.setValue(f"detached_chart_{index}_background", self.colors["background"].text())
            self._settings.setValue(f"detached_chart_{index}_holding", self.colors["holding"].text())
        for key, spin in self.thresholds.items(): self._settings.setValue(f"detached_{key}_trade_value", spin.value())

    def _choose(self, target: QLineEdit, title: str) -> None:
        color = QColorDialog.getColor(QColor(target.text()), self, title)
        if color.isValid(): target.setText(color.name())

    def _reset(self) -> None:
        for key, edit in self.colors.items(): edit.setText(self.DEFAULTS[key])
        for spin in self.thresholds.values(): spin.setValue(0.0)
