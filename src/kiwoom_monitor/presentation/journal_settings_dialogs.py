"""매매일지 기본 설정과 차트 설정 대화상자."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from kiwoom_monitor.application.strategy_pack import STRATEGY_RESULT_MODES, StrategyPackManifest
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.presentation.strategy_pack_dialogs import StrategyPackManagerDialog

MOVING_AVERAGE_DEFAULTS: dict[int, tuple[bool, str, float]] = {
    5: (True, "#EC407A", 1.5), 10: (True, "#1976D2", 1.5),
    20: (True, "#FBC02D", 1.5), 60: (True, "#8BC34A", 1.5),
    120: (True, "#424242", 1.5),
}

class JournalSettingsDialog(QDialog):
    def __init__(self, rules_path: str, estimated_buy_cost_rate: float, estimated_sell_cost_rate: float, auto_history_sync: bool, chart_background: str,
                 ctrl_wheel_zoom: bool, trade_value_threshold: float, daily_trade_value_threshold: float,
                 show_trade_details: bool, drawing_line_width: float,
                 rectangle_color: str, line_color: str, horizontal_line_color: str, vertical_line_color: str,
                 strategy_packs: tuple[StrategyPackManifest, ...], strategy_result_mode: str,
                 repository: JournalRepository,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent); self.setWindowTitle("매매일지 설정"); self.resize(520, 260); self.reset_all = False
        self.strategy_packs = strategy_packs; self._repository = repository
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.rules_path = QLineEdit(rules_path); self.rules_path.setReadOnly(True); self.rules_path.setPlaceholderText("선택하지 않으면 기본 분석만 사용")
        choose_rules = QPushButton("파일 선택"); choose_rules.clicked.connect(self._choose_rules)
        clear_rules = QPushButton("해제"); clear_rules.clicked.connect(self.rules_path.clear)
        rules_row = QHBoxLayout(); rules_row.addWidget(self.rules_path); rules_row.addWidget(choose_rules); rules_row.addWidget(clear_rules)
        self.estimated_buy_cost_rate = QDoubleSpinBox(); self.estimated_buy_cost_rate.setRange(0.0, 5.0); self.estimated_buy_cost_rate.setDecimals(4)
        self.estimated_buy_cost_rate.setSingleStep(0.001); self.estimated_buy_cost_rate.setSuffix(" %"); self.estimated_buy_cost_rate.setValue(estimated_buy_cost_rate)
        self.estimated_sell_cost_rate = QDoubleSpinBox(); self.estimated_sell_cost_rate.setRange(0.0, 5.0); self.estimated_sell_cost_rate.setDecimals(4)
        self.estimated_sell_cost_rate.setSingleStep(0.001); self.estimated_sell_cost_rate.setSuffix(" %"); self.estimated_sell_cost_rate.setValue(estimated_sell_cost_rate)
        self.auto_history_sync = QCheckBox("NXT 종료 후(20:05 이후) 하루 한 번 자동으로 가져오기"); self.auto_history_sync.setChecked(auto_history_sync)
        self.ctrl_wheel_zoom = QCheckBox("차트에서 Ctrl + 마우스 휠로 확대·축소"); self.ctrl_wheel_zoom.setChecked(ctrl_wheel_zoom)
        self.trade_value_threshold = QDoubleSpinBox(); self.trade_value_threshold.setRange(0.0, 100_000.0)
        self.trade_value_threshold.setDecimals(2); self.trade_value_threshold.setSingleStep(10.0)
        self.trade_value_threshold.setSuffix(" 억"); self.trade_value_threshold.setSpecialValueText("사용 안 함")
        self.trade_value_threshold.setValue(trade_value_threshold)
        self.daily_trade_value_threshold = QDoubleSpinBox(); self.daily_trade_value_threshold.setRange(0.0, 1_000_000.0)
        self.daily_trade_value_threshold.setDecimals(0); self.daily_trade_value_threshold.setSingleStep(100.0)
        self.daily_trade_value_threshold.setSuffix(" 억"); self.daily_trade_value_threshold.setSpecialValueText("사용 안 함")
        self.daily_trade_value_threshold.setValue(daily_trade_value_threshold)
        self.show_trade_details = QCheckBox("차트에 체결시간·가격·수량 표시")
        self.show_trade_details.setChecked(show_trade_details)
        self.drawing_line_width = QDoubleSpinBox(); self.drawing_line_width.setRange(0.5, 10.0)
        self.drawing_line_width.setDecimals(1); self.drawing_line_width.setSingleStep(0.5); self.drawing_line_width.setValue(drawing_line_width)
        self.rectangle_color = QLineEdit(rectangle_color); self.rectangle_color.setReadOnly(True)
        choose_rectangle_color = QPushButton("색 선택"); choose_rectangle_color.clicked.connect(self._choose_rectangle_color)
        rectangle_color_row = QHBoxLayout(); rectangle_color_row.addWidget(self.rectangle_color); rectangle_color_row.addWidget(choose_rectangle_color)
        self.line_color = QLineEdit(line_color); self.line_color.setReadOnly(True)
        choose_line_color = QPushButton("색 선택"); choose_line_color.clicked.connect(lambda: self._choose_drawing_color(self.line_color, "일반 선 색상"))
        line_color_row = QHBoxLayout(); line_color_row.addWidget(self.line_color); line_color_row.addWidget(choose_line_color)
        self.horizontal_line_color = QLineEdit(horizontal_line_color); self.horizontal_line_color.setReadOnly(True)
        choose_horizontal_color = QPushButton("색 선택"); choose_horizontal_color.clicked.connect(lambda: self._choose_drawing_color(self.horizontal_line_color, "가로선 색상"))
        horizontal_color_row = QHBoxLayout(); horizontal_color_row.addWidget(self.horizontal_line_color); horizontal_color_row.addWidget(choose_horizontal_color)
        self.vertical_line_color = QLineEdit(vertical_line_color); self.vertical_line_color.setReadOnly(True)
        choose_vertical_color = QPushButton("색 선택"); choose_vertical_color.clicked.connect(lambda: self._choose_drawing_color(self.vertical_line_color, "세로선 색상"))
        vertical_color_row = QHBoxLayout(); vertical_color_row.addWidget(self.vertical_line_color); vertical_color_row.addWidget(choose_vertical_color)
        self.chart_background = QLineEdit(chart_background); self.chart_background.setReadOnly(True)
        choose_background = QPushButton("색 선택"); choose_background.clicked.connect(self._choose_background)
        background_row = QHBoxLayout(); background_row.addWidget(self.chart_background); background_row.addWidget(choose_background)
        manage_packs = QPushButton("전략팩 관리…"); manage_packs.clicked.connect(self._manage_strategy_packs)
        self.strategy_pack_summary = QLabel(); self._update_strategy_pack_summary()
        strategy_row = QHBoxLayout(); strategy_row.addWidget(self.strategy_pack_summary); strategy_row.addWidget(manage_packs)
        self.strategy_result_mode = QComboBox()
        for key, label in STRATEGY_RESULT_MODES.items(): self.strategy_result_mode.addItem(label, key)
        mode_index = self.strategy_result_mode.findData(strategy_result_mode)
        self.strategy_result_mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.strategy_result_mode.setToolTip(
            "자동 교체는 검증된 전략팩이 기본분석보다 신뢰도 5%p 이상 높거나 기본분석이 기타일 때 적용합니다."
        )
        form.addRow("개인 매매 원칙", rules_row); form.addRow("자동분석 전략", strategy_row)
        form.addRow("분석 결과 표시", self.strategy_result_mode)
        form.addRow("정산 전 매수 비용률", self.estimated_buy_cost_rate)
        form.addRow("정산 전 매도 비용률", self.estimated_sell_cost_rate)
        form.addRow("키움 체결내역", self.auto_history_sync)
        layout.addLayout(form)
        note = QLabel("예정 비용은 매수금액과 매도금액에 각각 적용합니다. 정산 후에는 키움 실제 수수료·세금을 우선합니다. 초기화는 화면·분석 설정만 기본값으로 돌리며 체결·분봉·복기 기록은 삭제하지 않습니다.")
        note.setWordWrap(True); note.setStyleSheet("color:#666666;"); layout.addWidget(note)
        layout.addStretch(); buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        reset_button = buttons.addButton("설정 초기화", QDialogButtonBox.ButtonRole.ResetRole)
        reset_button.clicked.connect(self._reset_defaults)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("설정 저장"); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _choose_rules(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "개인 매매 원칙 파일 선택", self.rules_path.text(), "원칙 문서 (*.txt *.md *.docx);;모든 파일 (*)")
        if selected:
            self.rules_path.setText(selected)

    def _manage_strategy_packs(self) -> None:
        dialog = StrategyPackManagerDialog(self.strategy_packs, self._repository, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.strategy_packs = dialog.packs(); self._update_strategy_pack_summary()

    def _update_strategy_pack_summary(self) -> None:
        active = sum(pack.enabled and pack.review_status == "approved" for pack in self.strategy_packs)
        pending = sum(pack.review_status != "approved" for pack in self.strategy_packs)
        self.strategy_pack_summary.setText(f"사용 {active}개 · 검토 중 {pending}개")

    def _choose_background(self) -> None:
        color = QColorDialog.getColor(QColor(self.chart_background.text() or "#FFFFFF"), self, "차트 배경색")
        if color.isValid():
            self.chart_background.setText(color.name())

    def _choose_rectangle_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.rectangle_color.text() or "#7B1FA2"), self, "사각형 색상")
        if color.isValid():
            self.rectangle_color.setText(color.name())

    def _choose_drawing_color(self, target: QLineEdit, title: str) -> None:
        color = QColorDialog.getColor(QColor(target.text() or "#7B1FA2"), self, title)
        if color.isValid():
            target.setText(color.name())

    def _reset_defaults(self) -> None:
        self.reset_all = True
        self.rules_path.clear()
        self.estimated_buy_cost_rate.setValue(0.015)
        self.estimated_sell_cost_rate.setValue(0.215)
        self.auto_history_sync.setChecked(True)
        self.strategy_result_mode.setCurrentIndex(self.strategy_result_mode.findData("together"))


class JournalChartSettingsDialog(QDialog):
    def __init__(self, owner: "JournalWindow") -> None:
        super().__init__(owner); self.setWindowTitle("차트 설정"); self.resize(570, 620)
        layout = QVBoxLayout(self); form = QFormLayout()
        self.ctrl_wheel_zoom = QCheckBox("차트에서 Ctrl + 마우스 휠로 확대·축소")
        self.ctrl_wheel_zoom.setChecked(owner._ctrl_wheel_zoom_enabled)
        self.trade_value_threshold = QDoubleSpinBox(); self.trade_value_threshold.setRange(0.0, 100_000.0)
        self.trade_value_threshold.setDecimals(2); self.trade_value_threshold.setSingleStep(10.0)
        self.trade_value_threshold.setSuffix(" 억"); self.trade_value_threshold.setSpecialValueText("사용 안 함")
        self.trade_value_threshold.setValue(owner._trade_value_threshold)
        self.daily_trade_value_threshold = QDoubleSpinBox(); self.daily_trade_value_threshold.setRange(0.0, 1_000_000.0)
        self.daily_trade_value_threshold.setDecimals(0); self.daily_trade_value_threshold.setSingleStep(100.0)
        self.daily_trade_value_threshold.setSuffix(" 억"); self.daily_trade_value_threshold.setSpecialValueText("사용 안 함")
        self.daily_trade_value_threshold.setValue(owner._daily_trade_value_threshold)
        self.show_trade_details = QCheckBox("차트에 체결시간·가격·수량 표시")
        self.show_trade_details.setChecked(owner._show_trade_details)
        self.drawing_line_width = QDoubleSpinBox(); self.drawing_line_width.setRange(0.5, 10.0)
        self.drawing_line_width.setDecimals(1); self.drawing_line_width.setSingleStep(0.5); self.drawing_line_width.setValue(owner._drawing_line_width)
        self.chart_background, background_row = self._color_control(owner._chart_background, "차트 배경색")
        self.rectangle_color, rectangle_row = self._color_control(owner._rectangle_color, "사각형 색상")
        self.line_color, line_row = self._color_control(owner._line_color, "일반 선 색상")
        self.horizontal_line_color, horizontal_row = self._color_control(owner._horizontal_line_color, "가로선 색상")
        self.vertical_line_color, vertical_row = self._color_control(owner._vertical_line_color, "세로선 색상")
        self.moving_averages: dict[int, tuple[QCheckBox, QLineEdit, QDoubleSpinBox]] = {}
        form.addRow("차트 조작", self.ctrl_wheel_zoom)
        form.addRow("1분 거래대금 기준선", self.trade_value_threshold)
        form.addRow("일봉 거래대금 기준선", self.daily_trade_value_threshold)
        form.addRow("체결 꼬리표", self.show_trade_details); form.addRow("그리기 선 두께", self.drawing_line_width)
        form.addRow("차트 배경색", background_row); form.addRow("사각형 색상", rectangle_row)
        form.addRow("일반 선 색상", line_row); form.addRow("가로선 색상", horizontal_row); form.addRow("세로선 색상", vertical_row)
        for period, (_default_enabled, default_color, default_width) in MOVING_AVERAGE_DEFAULTS.items():
            enabled, color_value, width_value = owner._moving_average_styles.get(period, (True, default_color, default_width))
            check = QCheckBox(f"{period}선 표시"); check.setChecked(enabled)
            color, color_row = self._color_control(color_value, f"{period}선 색상")
            width = QDoubleSpinBox(); width.setRange(0.5, 6.0); width.setDecimals(1); width.setSingleStep(0.5); width.setValue(width_value)
            row = QHBoxLayout(); row.addWidget(check); row.addLayout(color_row); row.addWidget(QLabel("두께")); row.addWidget(width)
            form.addRow(f"이동평균 {period}", row)
            self.moving_averages[period] = (check, color, width)
        layout.addLayout(form); layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("설정 저장")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _color_control(self, value: str, title: str) -> tuple[QLineEdit, QHBoxLayout]:
        field = QLineEdit(value); field.setReadOnly(True)
        choose = QPushButton("색 선택")
        choose.clicked.connect(lambda _checked=False, target=field, caption=title: self._choose_color(target, caption))
        row = QHBoxLayout(); row.addWidget(field); row.addWidget(choose)
        return field, row

    def _choose_color(self, target: QLineEdit, title: str) -> None:
        color = QColorDialog.getColor(QColor(target.text() or "#FFFFFF"), self, title)
        if color.isValid(): target.setText(color.name())
