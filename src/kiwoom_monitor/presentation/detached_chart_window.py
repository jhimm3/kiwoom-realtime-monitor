"""매매일지의 최대 6개 차트 따로보기 패널과 독립 창."""

from __future__ import annotations

import html
import math
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent, QImage, QPainter, QPen, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea, QScrollBar,
    QSpinBox, QVBoxLayout, QWidget,
)

from kiwoom_monitor.application.journal_chart_layout import daily_chart_rows as _daily_chart_rows
from kiwoom_monitor.application.trade_journal_summary import TradeEpisode
from kiwoom_monitor.presentation.detached_chart_settings import DetachedChartSettingsDialog
from kiwoom_monitor.presentation.journal_chart_widget import MinuteChart

class DetachedChartPanel(QWidget):
    """전용 창에서 봉 주기와 조작 상태를 독립적으로 가지는 차트 패널."""

    state_changed = Signal(str, str, int, object)
    state_context_changed = Signal()

    def __init__(self, panel_index: int, interval: str, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent); self._index = panel_index; self._settings = settings; self._applying_shared_state = False
        self.setObjectName("DetachedChartPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self); layout.setContentsMargins(3, 3, 3, 3); layout.setSpacing(3)
        source_actions = QHBoxLayout(); chart_actions = QHBoxLayout()
        self.title_label = QLabel(f"차트 {panel_index + 1}"); self.title_label.setObjectName("PanelTitle")
        source_actions.addWidget(self.title_label)
        self.source = QComboBox(); self.source.setEditable(True); self.source.addItems(("현재 종목", "코스피 지수", "코스닥 지수"))
        self.source.setInsertPolicy(QComboBox.InsertPolicy.NoInsert); self._stock_codes: dict[str, str] = {}
        saved_source = settings.value(f"detached_chart_{panel_index}_source", "현재 종목", type=str)
        self.source.setCurrentText(saved_source if saved_source in {"현재 종목", "코스피 지수", "코스닥 지수"} else "현재 종목")
        self.source.setMinimumWidth(130); self.source.setMaximumWidth(260)
        self.show_trades = QCheckBox("B/S 체결 표시")
        self.show_trades.setChecked(settings.value(f"detached_chart_{panel_index}_show_trades", True, type=bool))
        self.show_details = QCheckBox("체결정보")
        self.show_details.setToolTip("체결시간·가격·수량 꼬리표 표시")
        self.show_details.setChecked(settings.value(f"detached_chart_{panel_index}_show_details", True, type=bool))
        self.interval = QComboBox(); self.interval.addItems(("1분", "3분", "5분", "10분", "30분", "60분", "일봉"))
        saved_interval = settings.value(f"detached_chart_{panel_index}_interval", interval, type=str)
        self.interval.setCurrentText(saved_interval if saved_interval in tuple(self.interval.itemText(i) for i in range(self.interval.count())) else interval)
        initial_count = settings.value(f"detached_chart_{panel_index}_count", 120, type=int)
        self.view_count = QSpinBox(); self.view_count.setRange(0, 2_000); self.view_count.setSpecialValueText("전체")
        self.view_count.setSuffix("개"); self.view_count.setValue(initial_count)
        zoom_in = QPushButton("확대"); zoom_out = QPushButton("축소"); show_all = QPushButton("전체")
        self.drawing = QComboBox(); self.drawing.addItems(("그리기 끄기", "선", "가로선", "세로선", "사각형", "텍스트"))
        clear = QPushButton("그림 초기화")
        for widget in (QLabel("대상"), self.source, self.show_trades, self.show_details): source_actions.addWidget(widget)
        source_actions.addStretch(); layout.addLayout(source_actions)
        for widget in (QLabel("봉 주기"), self.interval, QLabel("화면 봉 수"), self.view_count, zoom_in, zoom_out, show_all, self.drawing, clear): chart_actions.addWidget(widget)
        chart_actions.addStretch(); layout.addLayout(chart_actions)
        self.chart = MinuteChart(); self.chart.setMinimumHeight(155); layout.addWidget(self.chart, 1)
        self._episode: TradeEpisode | None = None; self._minute_rows: tuple[tuple[object, ...], ...] = ()
        self._daily_rows: tuple[tuple[object, ...], ...] = (); self._setup_types: tuple[str, ...] = ()
        self._index_rows: dict[str, tuple[tuple[object, ...], ...]] = {}
        self.scroll = QScrollBar(Qt.Orientation.Horizontal); layout.addWidget(self.scroll)
        self.interval.currentTextChanged.connect(self.chart.set_interval)
        self.interval.currentTextChanged.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_interval", value))
        self.interval.currentTextChanged.connect(self._chart_context_changed)
        self.view_count.valueChanged.connect(self.chart.set_view_count)
        self.view_count.valueChanged.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_count", value))
        zoom_in.clicked.connect(lambda: self.chart.zoom(0.5)); zoom_out.clicked.connect(lambda: self.chart.zoom(2.0))
        show_all.clicked.connect(lambda: self.view_count.setValue(0)); clear.clicked.connect(self.chart.clear_annotations)
        self.drawing.currentTextChanged.connect(lambda value: self.chart.set_drawing_mode(value.replace("그리기 ", "")))
        self.scroll.valueChanged.connect(self.chart.set_view_start)
        self.chart.view_changed.connect(self._sync_scroll)
        self.chart.view_count_changed.connect(self._sync_count)
        self.chart.view_count_changed.connect(lambda _count: self._emit_state())
        self.chart.annotations_changed.connect(lambda _annotations: self._emit_state())
        self.chart.set_interval(self.interval.currentText()); self.chart.set_view_count(initial_count)
        self.view_count.blockSignals(True); self.view_count.setValue(initial_count); self.view_count.blockSignals(False)
        self.source.currentTextChanged.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_source", value))
        self.source.currentTextChanged.connect(self._apply_source)
        self.source.currentTextChanged.connect(self._chart_context_changed)
        self.show_trades.toggled.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_show_trades", value))
        self.show_trades.toggled.connect(self._apply_source)
        self.show_details.toggled.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_show_details", value))
        self.show_details.toggled.connect(self.chart.set_trade_details_visible)

    def configure(self, background: str, grid: str, foreground: str, up: str, down: str, accent: str,
                  ctrl_zoom: bool, minute_threshold: float, daily_threshold: float, show_details: bool,
                  drawing_width: float, rectangle: str, line: str, horizontal: str, vertical: str) -> None:
        panel_accent = self._settings.value(f"detached_chart_{self._index}_accent", accent, type=str)
        self._last_configuration = (background, grid, foreground, up, down, accent, ctrl_zoom, minute_threshold,
                                    daily_threshold, show_details, drawing_width, rectangle, line, horizontal, vertical)
        # 본창과 따로보기는 같은 차트 설정을 사용한다. 예전 detached 전용 색 설정은
        # 복구 호환을 위해 QSettings에 남기되 더 이상 렌더링 기준으로 사용하지 않는다.
        self.chart.set_background(background); self.chart.set_ctrl_wheel_zoom_enabled(ctrl_zoom)
        self.chart.set_holding_highlight_color("#FFEB96")
        self._base_minute_threshold, self._base_daily_threshold = minute_threshold, daily_threshold
        self._apply_trade_value_thresholds()
        if not self._settings.contains(f"detached_chart_{self._index}_show_details"):
            self.show_details.setChecked(show_details)
        self.chart.set_trade_details_visible(self.show_details.isChecked())
        self.chart.set_drawing_style(drawing_width, rectangle, line, horizontal, vertical)
        self.chart.set_text_style("#263238", 12)
        color = QColor(panel_accent); accent_value = color.name() if color.isValid() else "#3F6FA0"
        self._accent_value = accent_value
        self.setStyleSheet(
            "QWidget#DetachedChartPanel{background-color:#FFFFFF;border:1px solid #BFC6CF;border-radius:0px;}"
        )
        self.title_label.setStyleSheet(f"background:{accent_value};color:white;font-weight:700;padding:3px 9px;border-radius:3px;")

    def set_data(self, episode: TradeEpisode | None, minute_rows: tuple[tuple[object, ...], ...],
                 daily_rows: tuple[tuple[object, ...], ...], setup_types: tuple[str, ...],
                 index_rows: dict[str, tuple[tuple[object, ...], ...]] | None = None) -> None:
        self._episode, self._minute_rows, self._daily_rows, self._setup_types = episode, minute_rows, daily_rows, setup_types
        self._index_rows = index_rows or {}; self._apply_source()

    def set_stock_choices(self, stocks: tuple[tuple[str, str], ...]) -> None:
        current = self.source.currentText(); self.source.blockSignals(True)
        self.source.clear(); self.source.addItems(("현재 종목", "코스피 지수", "코스닥 지수"))
        self._stock_codes = {f"{name} · {code}": code for code, name in stocks}
        self.source.addItems(tuple(self._stock_codes)); self.source.setCurrentText(current)
        self.source.blockSignals(False)

    def selected_stock_code(self) -> str:
        return self._stock_codes.get(self.source.currentText(), "")

    def source_id(self) -> str:
        source_key = {"코스피 지수": "index:kospi", "코스닥 지수": "index:kosdaq"}.get(self.source.currentText())
        if source_key:
            return source_key
        custom_code = self.selected_stock_code()
        if custom_code:
            return f"stock:{custom_code}"
        if self._episode is not None:
            return f"stock:{self._episode.summary.stock_code}"
        return ""

    def apply_shared_state(self, view_count: int, annotations: tuple[tuple[object, ...], ...]) -> None:
        self._applying_shared_state = True
        try:
            self.view_count.blockSignals(True); self.view_count.setValue(max(0, int(view_count))); self.view_count.blockSignals(False)
            self.chart.set_view_count(view_count)
            self.chart.set_annotations(annotations)
        finally:
            self._applying_shared_state = False

    def _emit_state(self) -> None:
        if self._applying_shared_state:
            return
        source_id = self.source_id()
        if source_id:
            self.state_changed.emit(source_id, self.interval.currentText(), self.chart.view_count(), self.chart.annotations())

    def _chart_context_changed(self, *_args: object) -> None:
        self.chart.set_annotations(())
        self.state_context_changed.emit()

    def _apply_source(self, *_args: object) -> None:
        previous_sync = self._applying_shared_state
        self._applying_shared_state = True
        source_key = {"코스피 지수": "kospi", "코스닥 지수": "kosdaq"}.get(self.source.currentText())
        custom_code = self.selected_stock_code()
        self.chart.set_index_mode(bool(source_key))
        self.chart.set_context_fills(self._episode.fills if self._episode is not None else ())
        if source_key:
            rows = self._index_rows.get(source_key, ())
            self.chart.set_rows(rows); self.chart.set_daily_rows(self._index_rows.get(f"daily:{source_key}", _daily_chart_rows(rows)))
            # 비교 차트는 체결 시각으로 보유 구간의 연한 노란 배경만 계산한다.
            # B/S·점·세로선은 MinuteChart의 reference 모드에서 그리지 않는다.
            self.chart.set_reference_fills(True); self.chart.set_fills(self._episode.fills if self._episode is not None else ()); self.chart.set_setup_types(())
        elif custom_code:
            rows = self._index_rows.get(custom_code, ())
            self.chart.set_rows(rows); self.chart.set_daily_rows(self._index_rows.get(f"daily:{custom_code}", _daily_chart_rows(rows)))
            self.chart.set_reference_fills(True); self.chart.set_fills(self._episode.fills if self._episode is not None else ()); self.chart.set_setup_types(())
        else:
            self.chart.set_rows(self._minute_rows); self.chart.set_daily_rows(self._daily_rows)
            self.chart.set_reference_fills(False); self.chart.set_fills(self._episode.fills if self._episode is not None and self.show_trades.isChecked() else ())
            self.chart.set_setup_types(self._setup_types)
        self.show_trades.setEnabled(not bool(source_key or custom_code))
        self.chart.set_interval(self.interval.currentText())
        self._apply_trade_value_thresholds()
        self.chart.set_view_count(self.view_count.value())
        self.chart.update()
        if self._episode is not None and self._episode.fills: self.chart.focus_time(self._episode.fills[0].filled_at)
        self._applying_shared_state = previous_sync

    def _apply_trade_value_thresholds(self) -> None:
        source_key = {"코스피 지수": "kospi", "코스닥 지수": "kosdaq"}.get(self.source.currentText())
        if source_key:
            minute = float(self._settings.value(f"detached_{source_key}_minute_trade_value", 0.0))
            daily = float(self._settings.value(f"detached_{source_key}_daily_trade_value", 0.0))
        else:
            minute = getattr(self, "_base_minute_threshold", 0.0); daily = getattr(self, "_base_daily_threshold", 0.0)
        self.chart.set_trade_value_threshold(minute); self.chart.set_daily_trade_value_threshold(daily)

    def save_state(self) -> None:
        self._settings.setValue(f"detached_chart_{self._index}_interval", self.interval.currentText())
        self._settings.setValue(f"detached_chart_{self._index}_count", self.view_count.value())
        self._settings.setValue(f"detached_chart_{self._index}_source", self.source.currentText())
        self._settings.setValue(f"detached_chart_{self._index}_show_trades", self.show_trades.isChecked())
        self._settings.setValue(f"detached_chart_{self._index}_show_details", self.show_details.isChecked())

    def _sync_scroll(self, maximum: int, current: int, page: int) -> None:
        self.scroll.blockSignals(True); self.scroll.setRange(0, maximum); self.scroll.setPageStep(max(1, page))
        self.scroll.setValue(current); self.scroll.setVisible(maximum > 0); self.scroll.blockSignals(False)

    def _sync_count(self, count: int) -> None:
        self.view_count.blockSignals(True); self.view_count.setValue(max(0, int(count))); self.view_count.blockSignals(False)


class DetachedChartWindow(QMainWindow):
    """한 종목의 여러 주기 차트를 한 개 또는 여러 개 동시에 보는 창."""

    source_changed = Signal()
    chart_state_changed = Signal(str, str, int, object)

    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent); self._settings = settings; self.setWindowTitle("매매일지 차트 따로 보기")
        root = QWidget(); outer = QVBoxLayout(root); header = QHBoxLayout(); controls = QHBoxLayout()
        self.current_stock_label = QLabel("현재 종목: —")
        self.current_stock_label.setStyleSheet("font-size:18px;font-weight:800;color:#17365D;padding:3px 12px;")
        header.addWidget(self.current_stock_label)
        self.panel_checks = []
        for index, label in enumerate(tuple(f"차트 {value}" for value in range(1, 7))):
            check = QCheckBox(label); check.setChecked(settings.value(f"detached_chart_{index}_visible", index < 3, type=bool))
            check.toggled.connect(lambda checked, target=index: settings.setValue(f"detached_chart_{target}_visible", checked))
            check.toggled.connect(self._reflow); self.panel_checks.append(check); header.addWidget(check)
        header.addStretch(); outer.addLayout(header)
        controls.addWidget(QLabel("배치")); self.layout_mode = QComboBox(); self.layout_mode.addItems(("세로", "격자"))
        self.layout_mode.setCurrentText(settings.value("detached_chart_layout", "세로", type=str))
        self.layout_mode.currentTextChanged.connect(lambda value: settings.setValue("detached_chart_layout", value))
        self.layout_mode.currentTextChanged.connect(self._reflow); controls.addWidget(self.layout_mode)
        self.include_review = QCheckBox("복기내용 포함"); self.include_review.setChecked(settings.value("detached_chart_export_review", True, type=bool))
        self.include_review.toggled.connect(lambda value: settings.setValue("detached_chart_export_review", value))
        export = QPushButton("현재 구성 이미지 저장"); export.clicked.connect(self._export_current_view)
        chart_settings = QPushButton("⚙ 차트 설정"); chart_settings.clicked.connect(self._open_settings)
        controls.addStretch(); controls.addWidget(self.include_review); controls.addWidget(export); controls.addWidget(chart_settings)
        outer.addLayout(controls); self.panel_host = QWidget(); self.panel_layout = QGridLayout(self.panel_host)
        self.panel_layout.setContentsMargins(0, 0, 0, 0); self.panel_layout.setHorizontalSpacing(0); self.panel_layout.setVerticalSpacing(0)
        self.panel_scroll = QScrollArea(); self.panel_scroll.setWidgetResizable(True); self.panel_scroll.setWidget(self.panel_host)
        outer.addWidget(self.panel_scroll, 1); self.setCentralWidget(root); self.setMinimumSize(520, 360)
        defaults = ("1분", "5분", "일봉", "1분", "1분", "일봉")
        if not settings.contains("detached_chart_3_source"): settings.setValue("detached_chart_3_source", "코스피 지수")
        if not settings.contains("detached_chart_4_source"): settings.setValue("detached_chart_4_source", "코스닥 지수")
        self.panels = tuple(DetachedChartPanel(index, value, settings, self.panel_host) for index, value in enumerate(defaults))
        self._shared_chart_states: dict[tuple[str, str], tuple[int, tuple[tuple[object, ...], ...]]] = {}
        for panel in self.panels:
            panel.state_changed.connect(self._panel_state_changed)
            panel.state_context_changed.connect(lambda target=panel: self._restore_panel_state(target))
        # 검색어를 입력하는 매 글자마다 DB/API 보완을 시작하지 않고 실제 항목을 선택했을 때만 요청한다.
        for panel in self.panels: panel.source.activated.connect(lambda _index: self.source_changed.emit())
        geometry = settings.value("detached_chart_geometry")
        if geometry is not None: self.restoreGeometry(geometry)
        else: self.resize(1200, 900)
        self._reflow()

    def configure(self, background: str, ctrl_zoom: bool, minute_threshold: float, daily_threshold: float,
                  show_details: bool, drawing_width: float, rectangle: str, line: str, horizontal: str, vertical: str) -> None:
        visual = {
            key: self._settings.value(f"detached_chart_color_{key}", default, type=str)
            for key, default in DetachedChartSettingsDialog.DEFAULTS.items()
        }
        for index, panel in enumerate(self.panels):
            panel.configure(
                background, visual["grid"], visual["foreground"], visual["up"], visual["down"], visual[f"accent_{index}"],
                ctrl_zoom, minute_threshold, daily_threshold, show_details,
                drawing_width, rectangle, line, horizontal, vertical,
            )

    def set_data(self, episode: TradeEpisode | None, minute_rows: tuple[tuple[object, ...], ...],
                 daily_rows: tuple[tuple[object, ...], ...], setup_types: tuple[str, ...],
                 index_rows: dict[str, tuple[tuple[object, ...], ...]] | None = None) -> None:
        name = (episode.summary.stock_name or episode.summary.stock_code) if episode is not None else "—"
        self.current_stock_label.setText(f"현재 종목: {name}")
        for panel in self.panels:
            panel.set_data(episode, minute_rows, daily_rows, setup_types, index_rows)
            self._restore_panel_state(panel)

    def set_shared_chart_state(self, source_id: str, interval: str, view_count: int,
                               annotations: tuple[tuple[object, ...], ...]) -> None:
        key = (source_id, interval)
        value = (max(0, int(view_count)), tuple(tuple(item) for item in annotations))
        self._shared_chart_states[key] = value
        for panel in self.panels:
            if (panel.source_id(), panel.interval.currentText()) == key:
                panel.apply_shared_state(*value)

    def _restore_panel_state(self, panel: DetachedChartPanel) -> None:
        state = self._shared_chart_states.get((panel.source_id(), panel.interval.currentText()))
        if state is not None:
            panel.apply_shared_state(*state)

    def _panel_state_changed(self, source_id: str, interval: str, view_count: int, annotations: object) -> None:
        normalized = tuple(tuple(value) for value in annotations) if isinstance(annotations, (tuple, list)) else ()
        self.set_shared_chart_state(source_id, interval, view_count, normalized)
        self.chart_state_changed.emit(source_id, interval, view_count, normalized)

    def set_stock_choices(self, stocks: tuple[tuple[str, str], ...]) -> None:
        for panel in self.panels: panel.set_stock_choices(stocks)

    def set_trade_details_visible(self, visible: bool) -> None:
        for panel in self.panels: panel.chart.set_trade_details_visible(visible)

    def selected_stock_codes(self) -> tuple[str, ...]:
        return tuple(sorted({
            code
            for panel, check in zip(self.panels, self.panel_checks)
            if check.isChecked() and (code := panel.selected_stock_code())
        }))

    def selected_index_markets(self) -> tuple[str, ...]:
        labels = {"코스피 지수": "kospi", "코스닥 지수": "kosdaq"}
        return tuple(sorted({
            labels[panel.source.currentText()]
            for panel, check in zip(self.panels, self.panel_checks)
            if check.isChecked() and panel.source.currentText() in labels
        }))

    def _reflow(self, *_args: object) -> None:
        if not hasattr(self, "panels"): return
        while self.panel_layout.count():
            self.panel_layout.takeAt(0)
        visible = [panel for panel, check in zip(self.panels, self.panel_checks) if check.isChecked()]
        for panel, check in zip(self.panels, self.panel_checks): panel.setVisible(check.isChecked())
        for index, panel in enumerate(visible):
            if self.layout_mode.currentText() == "격자" and len(visible) > 1:
                self.panel_layout.addWidget(panel, index // 2, index % 2)
            else:
                self.panel_layout.addWidget(panel, index, 0)

    def _open_settings(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "_open_chart_settings"):
            parent._open_chart_settings()

    def _export_current_view(self) -> None:
        visible = [panel for panel, check in zip(self.panels, self.panel_checks) if check.isChecked()]
        if not visible:
            QMessageBox.information(self, "현재 구성 이미지 저장", "표시할 차트를 하나 이상 선택하세요."); return
        parent = self.parent(); context = parent._detached_chart_export_context() if parent is not None and hasattr(parent, "_detached_chart_export_context") else None
        title = str(context[0]) if context else "매매일지 차트"
        safe = re.sub(r'[\\/:*?"<>|]+', "_", title)[:80]
        selected, _ = QFileDialog.getSaveFileName(self, "현재 차트 구성 이미지 저장", str(Path.home() / "Desktop" / f"{safe}_차트.png"), "PNG 이미지 (*.png)")
        if not selected: return
        output = Path(selected); output = output if output.suffix.lower() == ".png" else output.with_suffix(".png")
        grid = self.layout_mode.currentText() == "격자" and len(visible) > 1
        columns = min(2, len(visible)) if grid else 1; rows = math.ceil(len(visible) / columns)
        margin, gap, header_height, chart_height = 36, 20, 48, 520
        cell_width = 920 if grid else 1840; cell_height = header_height + chart_height
        content_width = columns * cell_width + (columns - 1) * gap

        title_doc = QTextDocument(); title_doc.setDefaultStyleSheet("body{font-family:'맑은 고딕';font-size:24px;color:#222}h1{font-size:38px;margin:0 0 8px 0}")
        title_doc.setHtml(f"<h1>{html.escape(title)}</h1><div>현재 보이는 차트 구성 · {datetime.now():%Y-%m-%d %H:%M}</div>")
        title_doc.setTextWidth(content_width); title_height = int(math.ceil(title_doc.size().height()))
        review_docs: list[QTextDocument] = []
        if self.include_review.isChecked() and context:
            for heading, body in context[1]:
                doc = QTextDocument(); doc.setDefaultStyleSheet(
                    "body{font-family:'맑은 고딕';font-size:22px;color:#222;line-height:1.3}h2{font-size:28px;color:#24496e;margin:0 0 7px 0}.box{background:#f4f6f8;padding:14px}"
                )
                doc.setHtml(f"<h2>{html.escape(str(heading))}</h2><div class='box'>{html.escape(str(body) or '—').replace(chr(10), '<br>')}</div>")
                doc.setTextWidth(content_width); review_docs.append(doc)
        reviews_height = sum(int(math.ceil(doc.size().height())) + gap for doc in review_docs)
        total_height = margin + title_height + gap + rows * cell_height + (rows - 1) * gap + (gap + reviews_height if review_docs else 0) + margin
        image = QImage(content_width + margin * 2, total_height, QImage.Format.Format_ARGB32); image.fill(QColor("#FFFFFF"))
        painter = QPainter(image); painter.translate(margin, margin); title_doc.drawContents(painter); painter.translate(0, title_height + gap)
        for order, panel in enumerate(visible):
            original_index = self.panels.index(panel); row, column = divmod(order, columns)
            x = column * (cell_width + gap); y = row * (cell_height + gap); accent = QColor(panel._accent_value)
            painter.fillRect(QRectF(x, y, cell_width, header_height), accent)
            painter.setPen(QColor("#FFFFFF")); font = painter.font(); font.setBold(True); font.setPointSize(15); painter.setFont(font)
            count_text = "전체" if panel.view_count.value() == 0 else f"{panel.view_count.value()}개"
            painter.drawText(QRectF(x + 14, y, cell_width - 28, header_height), Qt.AlignmentFlag.AlignVCenter, f"차트 {original_index + 1} · {panel.source.currentText()} · {panel.interval.currentText()} · 화면 봉 수 {count_text}")
            rendered = QImage(cell_width, chart_height, QImage.Format.Format_ARGB32); rendered.fill(panel.chart._background)
            chart_painter = QPainter(rendered); chart_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            chart_painter.scale(cell_width / max(1, panel.chart.width()), chart_height / max(1, panel.chart.height()))
            panel.chart.render(chart_painter, QPoint()); chart_painter.end(); painter.drawImage(int(x), int(y + header_height), rendered)
            painter.setPen(QPen(accent, 3)); painter.drawRect(QRectF(x, y, cell_width, cell_height))
        painter.translate(0, rows * cell_height + (rows - 1) * gap + (gap if review_docs else 0))
        for doc in review_docs:
            height = int(math.ceil(doc.size().height())); doc.drawContents(painter, QRectF(0, 0, content_width, height)); painter.translate(0, height + gap)
        painter.end()
        if image.save(str(output), "PNG"):
            QMessageBox.information(self, "현재 구성 이미지 저장", f"저장했습니다.\n{output}")
        else:
            QMessageBox.warning(self, "현재 구성 이미지 저장", "이미지를 저장하지 못했습니다.")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("detached_chart_geometry", self.saveGeometry())
        self._settings.setValue("detached_chart_layout", self.layout_mode.currentText())
        for index, (panel, check) in enumerate(zip(self.panels, self.panel_checks)):
            self._settings.setValue(f"detached_chart_{index}_visible", check.isChecked()); panel.save_state()
        super().closeEvent(event)
