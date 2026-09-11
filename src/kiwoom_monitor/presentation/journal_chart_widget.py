"""매매일지의 분봉·일봉 차트 위젯과 체결 꼬리표 도형."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QLineEdit, QWidget

from kiwoom_monitor.application.journal_chart_layout import (
    adaptive_price_grid_lines, average_price_label_positions, chart_close_information,
    chart_time_tick_indices, format_trade_value_eok, multi_day_time_tick_indices,
    rounded_price_grid, trade_callout_text, visible_trade_fills,
)
from kiwoom_monitor.application.trade_chart import aggregate_chart_rows
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import trade_fill_key
from kiwoom_monitor.presentation.journal_settings_dialogs import MOVING_AVERAGE_DEFAULTS

def trade_marker_polygon(x: int, price_y: int, side: str) -> QPolygon:
    """체결가를 꼭짓점으로 가리키는 HTS형 B/S 꼬리표."""
    half_width, shoulder, body_height = 10, 5, 24
    if side == "매수":
        return QPolygon((
            QPoint(x, price_y), QPoint(x + shoulder, price_y + 6),
            QPoint(x + half_width, price_y + 6), QPoint(x + half_width, price_y + body_height),
            QPoint(x - half_width, price_y + body_height), QPoint(x - half_width, price_y + 6),
            QPoint(x - shoulder, price_y + 6),
        ))
    return QPolygon((
        QPoint(x - half_width, price_y - body_height), QPoint(x + half_width, price_y - body_height),
        QPoint(x + half_width, price_y - 6), QPoint(x + shoulder, price_y - 6),
        QPoint(x, price_y), QPoint(x - shoulder, price_y - 6), QPoint(x - half_width, price_y - 6),
    ))


class MinuteChart(QWidget):
    """외부 브라우저 없이 그리는 가벼운 장중 분봉 차트."""
    view_changed = Signal(int, int, int)
    view_count_changed = Signal(int)
    annotations_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__(); self._raw_rows: tuple[tuple[object, ...], ...] = (); self._all_rows: tuple[tuple[object, ...], ...] = (); self._rows: tuple[tuple[object, ...], ...] = ()
        self._daily_rows: tuple[tuple[object, ...], ...] = ()
        self._fills: tuple[TradeFill, ...] = ()
        self._context_fills: tuple[TradeFill, ...] = ()
        self._reference_fills = False
        self._index_mode = False
        self._setup_types: tuple[str, ...] = ()
        self._interval = "1분"
        self._view_count = 120
        self._view_start = 0
        self._hover_index = -1
        self._background = QColor("#FFFFFF")
        self._foreground = QColor("#555555")
        self._grid = QColor("#E8E8E8")
        self._up_color = QColor("#D32F2F")
        self._down_color = QColor("#1976D2")
        self._holding_highlight_color = QColor("#FFEB96")
        self._top_margin = 50.0
        self._ctrl_wheel_zoom_enabled = True
        self._trade_value_threshold = 0.0
        self._daily_trade_value_threshold = 0.0
        self._show_trade_details = True
        self._drawing_mode = "끄기"
        self._annotations: list[tuple[object, ...]] = []
        self._drawing_start: tuple[datetime, float] | None = None
        self._drawing_preview: tuple[datetime, float] | None = None
        self._annotation_fill_keys: tuple[str, ...] = ()
        self._drawing_line_width = 2.0
        self._rectangle_color = QColor("#7B1FA2")
        self._line_color = QColor("#7B1FA2")
        self._horizontal_line_color = QColor("#F57C00")
        self._vertical_line_color = QColor("#00897B")
        self._text_color = QColor("#263238")
        self._text_size = 12
        self._moving_average_styles = dict(MOVING_AVERAGE_DEFAULTS)
        self._text_editor: QLineEdit | None = None
        self._editing_text_index: int | None = None
        self._selected_annotation: int | None = None
        self._pan_drag_origin: tuple[float, int] | None = None
        self.setMinimumHeight(155)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_background(self, color: str) -> None:
        background = QColor(color)
        if not background.isValid():
            background = QColor("#FFFFFF")
        self._background = background
        luminance = (background.red() * 299 + background.green() * 587 + background.blue() * 114) / 1000
        self._foreground = QColor("#E6E6E6" if luminance < 128 else "#555555")
        self._grid = QColor("#666666" if luminance < 128 else "#E0E0E0")
        self.update()

    def set_visual_style(self, background: str, grid: str, foreground: str, up: str, down: str) -> None:
        self.set_background(background)
        for attribute, value, fallback in (
            ("_grid", grid, "#D8DDE5"), ("_foreground", foreground, "#3C4653"),
            ("_up_color", up, "#D32F2F"), ("_down_color", down, "#1976D2"),
        ):
            color = QColor(value)
            setattr(self, attribute, color if color.isValid() else QColor(fallback))
        self.update()

    def set_holding_highlight_color(self, value: str) -> None:
        color = QColor(value)
        self._holding_highlight_color = color if color.isValid() else QColor("#FFEB96")
        self.update()

    def set_top_margin(self, value: float) -> None:
        self._top_margin = max(28.0, float(value)); self.update()

    def _holding_highlight_brush(self) -> QColor:
        color = QColor(self._holding_highlight_color); color.setAlpha(55)
        return color

    def set_ctrl_wheel_zoom_enabled(self, enabled: bool) -> None:
        self._ctrl_wheel_zoom_enabled = bool(enabled)

    def set_trade_value_threshold(self, value: float) -> None:
        self._trade_value_threshold = max(0.0, float(value)); self.update()

    def set_daily_trade_value_threshold(self, value: float) -> None:
        self._daily_trade_value_threshold = max(0.0, float(value)); self.update()

    def set_trade_details_visible(self, visible: bool) -> None:
        self._show_trade_details = bool(visible); self.update()

    def set_moving_average_styles(self, styles: dict[int, tuple[bool, str, float]]) -> None:
        normalized: dict[int, tuple[bool, str, float]] = {}
        for period, defaults in MOVING_AVERAGE_DEFAULTS.items():
            enabled, color_text, width = styles.get(period, defaults)
            color = QColor(color_text)
            normalized[period] = (
                bool(enabled), color.name() if color.isValid() else defaults[1],
                max(0.5, min(6.0, float(width))),
            )
        self._moving_average_styles = normalized
        self.update()


    def set_drawing_mode(self, mode: str) -> None:
        self._finish_text_edit()
        self._drawing_mode = mode if mode in ("선", "가로선", "세로선", "사각형", "텍스트") else "끄기"
        self._drawing_start = None; self._drawing_preview = None
        if self._drawing_mode == "끄기":
            self._selected_annotation = None
        self.setCursor(Qt.CursorShape.CrossCursor if self._drawing_mode != "끄기" else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_text_style(self, color: str, size: int) -> None:
        value = QColor(color)
        self._text_color = value if value.isValid() else QColor("#263238")
        self._text_size = max(6, min(72, int(size)))
        self.update()

    def _begin_text_edit(self, annotation_index: int, screen_position: object) -> None:
        self._finish_text_edit()
        if not (0 <= annotation_index < len(self._annotations)):
            return
        annotation = self._annotations[annotation_index]
        current = str(annotation[5]) if len(annotation) > 5 else ""
        editor = QLineEdit(self); editor.setText(current)
        font = editor.font(); font.setPointSize(self._text_size); editor.setFont(font)
        editor.setStyleSheet(
            f"QLineEdit{{color:{self._text_color.name()};background:rgba(255,255,255,225);"
            "border:1px solid #4A90E2;padding:1px 3px;}}"
        )
        metrics = editor.fontMetrics(); width = max(120, metrics.horizontalAdvance(current or "텍스트 입력") + 28)
        x = int(screen_position.x()) if hasattr(screen_position, "x") else 20
        y = int(screen_position.y()) - metrics.height() if hasattr(screen_position, "y") else 20
        editor.setGeometry(max(0, min(x, self.width() - width)), max(0, min(y, self.height() - 32)), width, 30)
        self._text_editor = editor; self._editing_text_index = annotation_index
        editor.editingFinished.connect(self._finish_text_edit)
        editor.show(); editor.setFocus(); editor.selectAll()

    def _finish_text_edit(self) -> None:
        editor = self._text_editor; index = self._editing_text_index
        if editor is None:
            return
        self._text_editor = None; self._editing_text_index = None
        value = editor.text().strip(); editor.deleteLater()
        if index is not None and 0 <= index < len(self._annotations):
            if value:
                self._annotations[index] = (*self._annotations[index][:5], value)
            else:
                del self._annotations[index]; self._selected_annotation = None
            self.annotations_changed.emit(tuple(self._annotations))
        self.update()

    def set_drawing_style(
        self, line_width: float, rectangle_color: str,
        line_color: str = "#7B1FA2", horizontal_line_color: str = "#F57C00", vertical_line_color: str = "#00897B",
    ) -> None:
        self._drawing_line_width = max(0.5, min(10.0, float(line_width)))
        color = QColor(rectangle_color)
        self._rectangle_color = color if color.isValid() else QColor("#7B1FA2")
        normal = QColor(line_color); horizontal = QColor(horizontal_line_color); vertical = QColor(vertical_line_color)
        self._line_color = normal if normal.isValid() else QColor("#7B1FA2")
        self._horizontal_line_color = horizontal if horizontal.isValid() else QColor("#F57C00")
        self._vertical_line_color = vertical if vertical.isValid() else QColor("#00897B")
        self.update()

    def clear_annotations(self) -> None:
        if self._text_editor is not None:
            self._text_editor.deleteLater(); self._text_editor = None; self._editing_text_index = None
        changed = bool(self._annotations)
        self._annotations.clear(); self._selected_annotation = None
        self._drawing_start = None; self._drawing_preview = None; self.update()
        if changed:
            self.annotations_changed.emit(())

    def annotations(self) -> tuple[tuple[object, ...], ...]:
        return tuple(self._annotations)

    def set_annotations(self, annotations: tuple[tuple[object, ...], ...]) -> None:
        self._finish_text_edit()
        self._annotations = [tuple(value) for value in annotations]
        self._selected_annotation = None
        self._drawing_start = None; self._drawing_preview = None
        self.update()

    def view_count(self) -> int:
        return self._view_count

    def set_rows(self, rows: tuple[tuple[object, ...], ...]) -> None:
        self._raw_rows = rows
        values = self._daily_rows if self._interval == "일봉" and self._daily_rows else aggregate_chart_rows(rows, self._interval)
        self._set_all_rows(values, stick_to_end=True)

    def set_daily_rows(self, rows: tuple[tuple[object, ...], ...]) -> None:
        self._daily_rows = rows
        if self._interval == "일봉":
            self._set_all_rows(rows, stick_to_end=True)

    def clear_daily_rows(self) -> None:
        self._daily_rows = ()

    def set_interval(self, interval: str) -> None:
        self._interval = interval
        values = self._daily_rows if interval == "일봉" and self._daily_rows else aggregate_chart_rows(self._raw_rows, interval)
        self._set_all_rows(values, stick_to_end=True)

    def set_view_start(self, start: int) -> None:
        self._view_start = max(0, min(int(start), self._maximum_view_start()))
        self._apply_view()

    def zoom(self, factor: float) -> None:
        total = len(self._all_rows)
        if not total:
            return
        old_count = total if self._view_count <= 0 else min(self._view_count, total)
        self.set_view_count(max(1, min(total, int(round(old_count * factor)))))

    def set_view_count(self, count: int) -> None:
        center = self._zoom_anchor_index()
        new_count = max(0, int(count))
        count_changed = new_count != self._view_count
        self._view_count = new_count
        visible_count = len(self._all_rows) if self._view_count <= 0 else self._view_count
        self._view_start = max(0, min(center - visible_count // 2, self._maximum_view_start()))
        self._apply_view()
        if count_changed:
            self.view_count_changed.emit(self._view_count)

    def _zoom_anchor_index(self) -> int:
        if not self._all_rows:
            return 0
        if self._fills:
            first = min(fill.filled_at for fill in self._fills)
            last = max(fill.filled_at for fill in self._fills)
            target = first + (last - first) / 2
            return min(
                range(len(self._all_rows)),
                key=lambda candidate: abs((datetime.fromisoformat(str(self._all_rows[candidate][0])) - target).total_seconds()),
            )
        current_count = len(self._all_rows) if self._view_count <= 0 else min(self._view_count, len(self._all_rows))
        return min(len(self._all_rows) - 1, self._view_start + current_count // 2)

    def show_all(self) -> None:
        count_changed = self._view_count != 0
        self._view_count = 0; self._view_start = 0; self._apply_view()
        if count_changed:
            self.view_count_changed.emit(self._view_count)

    def focus_time(self, target: datetime) -> None:
        if not self._all_rows:
            return
        index = min(
            range(len(self._all_rows)),
            key=lambda candidate: abs((datetime.fromisoformat(str(self._all_rows[candidate][0])) - target).total_seconds()),
        )
        count = len(self._all_rows) if self._view_count <= 0 else self._view_count
        self._view_start = max(0, min(index - count // 2, self._maximum_view_start()))
        self._apply_view()

    def _set_all_rows(self, rows: tuple[tuple[object, ...], ...], *, stick_to_end: bool) -> None:
        self._all_rows = rows
        if stick_to_end:
            self._view_start = self._maximum_view_start()
        self._apply_view()

    def _maximum_view_start(self) -> int:
        count = len(self._all_rows) if self._view_count <= 0 else self._view_count
        return max(0, len(self._all_rows) - count)

    def _apply_view(self) -> None:
        count = len(self._all_rows) if self._view_count <= 0 else self._view_count
        self._view_start = max(0, min(self._view_start, self._maximum_view_start()))
        self._rows = self._all_rows[self._view_start:self._view_start + count]
        self._hover_index = -1
        self.view_changed.emit(self._maximum_view_start(), self._view_start, min(count, len(self._all_rows)))
        self.update()

    def set_fills(self, fills: tuple[TradeFill, ...]) -> None:
        keys = tuple(trade_fill_key(fill) for fill in fills)
        if self._annotation_fill_keys and keys != self._annotation_fill_keys:
            self.clear_annotations()
        self._annotation_fill_keys = keys
        self._fills = fills; self.update()

    def set_context_fills(self, fills: tuple[TradeFill, ...]) -> None:
        """표식 표시 여부와 무관하게 종가·전일 대비의 기준 거래일을 유지한다."""
        self._context_fills = fills
        self.update()

    def set_reference_fills(self, enabled: bool) -> None:
        self._reference_fills = bool(enabled); self.update()

    def set_index_mode(self, enabled: bool) -> None:
        self._index_mode = bool(enabled); self.update()

    def set_setup_types(self, setup_types: tuple[str, ...]) -> None:
        self._setup_types = tuple(str(value) for value in setup_types)
        self.update()

    def setup_types(self) -> tuple[str, ...]:
        return self._setup_types

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._background)
        if not self._rows:
            painter.setPen(self._foreground); painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "분봉 데이터가 없습니다."); return
        row_minutes = [datetime.fromisoformat(str(row[0])) for row in self._rows]
        # 전일+당일 분봉을 함께 그리므로 첫 번째 봉의 날짜로 체결을 제한하면
        # 당일 B/S 표식이 전부 사라진다. 화면 전체 시간 범위에서 선별한다.
        day_fills = self._fills
        visible_end = row_minutes[-1] + (timedelta(days=1) if self._interval == "일봉" else timedelta(minutes=1))
        visible_fills = visible_trade_fills(day_fills, row_minutes[0], visible_end)
        prices = [float(value) for row in self._rows for value in row[1:5]]
        if not self._reference_fills: prices += [float(fill.price) for fill in visible_fills]
        raw_low, raw_high = min(prices), max(prices)
        price_padding = max(1.0, (raw_high - raw_low) * 0.08)
        grid_lines = adaptive_price_grid_lines(len(self._rows), self.height())
        low, high, price_step = rounded_price_grid(raw_low - price_padding, raw_high + price_padding, grid_lines)
        span = max(1.0, high - low)
        left, right, top, bottom = 14.0, 72.0, self._top_margin, 24.0
        price_bottom = max(top + 80.0, self.height() * 0.73)
        volume_top = price_bottom + 10.0
        volume_bottom = self.height() - bottom
        plot_width = max(1.0, self.width() - left - right)
        width = plot_width / len(self._rows)
        def y(price: float) -> float:
            return top + (high - price) / span * max(1.0, price_bottom - top)

        information = chart_close_information(self._context_fills or day_fills, self._all_rows, index_mode=self._index_mode)
        available_width = max(1, int(self.width() - left - right))
        painter.setPen(self._foreground)
        enabled_averages = tuple(
            (period, color) for period, (enabled, color, _width) in self._moving_average_styles.items() if enabled
        )
        legend_width = 0
        if enabled_averages:
            legend_width = painter.fontMetrics().horizontalAdvance("  ·  종가 단순 ") + sum(
                painter.fontMetrics().horizontalAdvance(str(period)) + 8 for period, _color in enabled_averages
            )
        information_width = max(1, available_width - legend_width)
        elided = painter.fontMetrics().elidedText(information, Qt.TextElideMode.ElideRight, information_width)
        painter.drawText(QRectF(left, 4, available_width, 20), Qt.AlignmentFlag.AlignVCenter, elided)
        if enabled_averages:
            legend_x = left + painter.fontMetrics().horizontalAdvance(elided)
            prefix = "  ·  종가 단순 "
            painter.setPen(QColor("#EC407A")); painter.drawText(int(legend_x), 19, prefix)
            legend_x += painter.fontMetrics().horizontalAdvance(prefix)
            for period, color in enabled_averages:
                label = str(period)
                painter.setPen(QColor(color)); painter.drawText(int(legend_x), 19, label)
                legend_x += painter.fontMetrics().horizontalAdvance(label) + 8

        fill_indices = {
            trade_fill_key(fill): min(range(len(row_minutes)), key=lambda candidate: abs((row_minutes[candidate] - fill.filled_at).total_seconds()))
            for fill in day_fills
        }
        position = 0; holding_start = -1; cycle_index = 0
        holding_spans: list[tuple[int, int, str]] = []
        for fill in sorted(day_fills, key=lambda value: value.filled_at):
            index = fill_indices[trade_fill_key(fill)]
            if fill.side == "매수":
                if position <= 0:
                    holding_start = index
                position += fill.quantity
            elif position > 0:
                position = max(0, position - fill.quantity)
                if position == 0 and holding_start >= 0:
                    start_x = left + holding_start * width
                    end_x = left + (index + 1) * width
                    painter.fillRect(QRectF(start_x, top, max(width, end_x - start_x), price_bottom - top), self._holding_highlight_brush())
                    setup_type = self._setup_types[cycle_index] if cycle_index < len(self._setup_types) else ""
                    holding_spans.append((holding_start, index + 1, setup_type))
                    cycle_index += 1
                    holding_start = -1
        if holding_start >= 0:
            painter.fillRect(QRectF(left + holding_start * width, top, plot_width - holding_start * width, price_bottom - top), self._holding_highlight_brush())
            setup_type = self._setup_types[cycle_index] if cycle_index < len(self._setup_types) else ""
            holding_spans.append((holding_start, len(self._rows), setup_type))
        for index, (start_index, end_index, setup_type) in enumerate(holding_spans, 1):
            if not setup_type:
                continue
            start_x = left + start_index * width
            end_x = left + end_index * width
            label_width = max(1.0, end_x - start_x)
            label = f"{index}차 {setup_type}" if len(self._setup_types) > 1 else setup_type
            label = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, max(1, int(label_width - 8)))
            # 보유구간 안쪽에 쓰면 캔들·가격 글씨와 겹치므로, 차트 상단에
            # 확보한 여백에서 노란 박스 윗선 바로 위에 붙여 표시한다.
            label_rect = QRectF(start_x, top - 22, label_width, 20)
            painter.fillRect(label_rect, QColor(255, 224, 90, 185))
            painter.setPen(QColor("#5D4B00"))
            painter.drawText(label_rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, label)

        grid_count = max(1, int(round((high - low) / price_step)))
        painter.setPen(QPen(self._grid, 1))
        for step in range(grid_count + 1):
            grid_y = top + (price_bottom - top) * step / grid_count
            painter.drawLine(int(left), int(grid_y), int(self.width() - right), int(grid_y))
            label_price = high - price_step * step
            price_label = f"{label_price:,.2f}" if self._index_mode else f"{int(label_price):,}"
            painter.setPen(self._foreground); painter.drawText(int(self.width() - right + 5), int(grid_y + 4), price_label)
            painter.setPen(QPen(self._grid, 1))

        trade_value_available = any(row[6] is not None for row in self._rows)
        trade_values = tuple(max(0.0, float(row[6] or 0)) for row in self._rows)
        visible_threshold = (
            self._daily_trade_value_threshold if self._interval == "일봉" else
            (self._trade_value_threshold if self._interval == "1분" else 0.0)
        )
        max_trade_value = max(1.0, max(trade_values, default=0.0), visible_threshold)
        for index, row in enumerate(self._rows):
            open_price, high_price, low_price, close_price = map(float, row[1:5])
            x = left + (index + 0.5) * width
            color = QColor(self._up_color if close_price >= open_price else self._down_color)
            painter.setPen(QPen(color, 1)); painter.drawLine(int(x), int(y(high_price)), int(x), int(y(low_price)))
            candle_top, candle_bottom = sorted((y(open_price), y(close_price)))
            painter.fillRect(QRectF(x - max(1.0, width * 0.3), candle_top, max(2.0, width * 0.6), max(1.0, candle_bottom - candle_top)), color)
            volume_height = trade_values[index] / max_trade_value * max(1.0, volume_bottom - volume_top)
            volume_color = QColor(color); volume_color.setAlpha(105)
            painter.fillRect(QRectF(x - max(0.5, width * 0.35), volume_bottom - volume_height, max(1.0, width * 0.7), volume_height), volume_color)

        # 화면 왼쪽 첫 봉도 이전 봉을 포함한 정확한 평균값을 쓰도록 전체 구간에서 계산한다.
        closes = [float(row[4]) for row in self._all_rows]
        for period, (enabled, color_text, line_width) in self._moving_average_styles.items():
            if not enabled or len(closes) < period:
                continue
            rolling = sum(closes[:period])
            values: dict[int, float] = {period - 1: rolling / period}
            for absolute_index in range(period, len(closes)):
                rolling += closes[absolute_index] - closes[absolute_index - period]
                values[absolute_index] = rolling / period
            points = [
                QPoint(int(left + (visible_index + 0.5) * width), int(y(values[self._view_start + visible_index])))
                for visible_index in range(len(self._rows))
                if self._view_start + visible_index in values
            ]
            if len(points) >= 2:
                painter.setPen(QPen(QColor(color_text), line_width))
                painter.drawPolyline(QPolygon(points))

        def annotation_x(moment: datetime) -> float:
            index = min(range(len(row_minutes)), key=lambda candidate: abs((row_minutes[candidate] - moment).total_seconds()))
            return left + (index + 0.5) * width

        annotations = list(self._annotations)
        if self._drawing_start is not None and self._drawing_preview is not None:
            annotations.append((self._drawing_mode, *self._drawing_start, *self._drawing_preview))
        painter.save(); painter.setClipRect(QRectF(left, top, plot_width, price_bottom - top))
        annotation_color = self._line_color
        for annotation_index, annotation in enumerate(annotations):
            shape, start_time, start_price, end_time, end_price, *extra = annotation
            start_point = QPoint(int(annotation_x(start_time)), int(y(start_price)))
            end_point = QPoint(int(annotation_x(end_time)), int(y(end_price)))
            shape_color = (
                self._rectangle_color if shape == "사각형" else
                self._horizontal_line_color if shape == "가로선" else
                self._vertical_line_color if shape == "세로선" else annotation_color
            )
            selected = annotation_index == self._selected_annotation and annotation_index < len(self._annotations)
            painter.setPen(QPen(shape_color, self._drawing_line_width + (1.5 if selected else 0.0), Qt.PenStyle.DashLine if selected else Qt.PenStyle.SolidLine))
            if shape == "텍스트":
                text_value = str(extra[0]) if extra else ""
                font = painter.font(); font.setPointSize(self._text_size); painter.setFont(font)
                metrics = painter.fontMetrics()
                text_rect = QRectF(start_point.x(), start_point.y() - metrics.height(), metrics.horizontalAdvance(text_value) + 6, metrics.height() + 4)
                if selected:
                    painter.setPen(QPen(self._text_color, 1, Qt.PenStyle.DashLine)); painter.drawRect(text_rect.adjusted(-2, -2, 2, 2))
                painter.setPen(self._text_color); painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text_value)
            elif shape == "사각형":
                rectangle = QRectF(start_point, end_point).normalized()
                fill_color = QColor(shape_color); fill_color.setAlpha(24)
                painter.fillRect(rectangle, fill_color); painter.drawRect(rectangle)
            elif shape == "가로선":
                painter.drawLine(QPoint(int(left), start_point.y()), QPoint(int(self.width() - right), start_point.y()))
            elif shape == "세로선":
                painter.drawLine(QPoint(start_point.x(), int(top)), QPoint(start_point.x(), int(price_bottom)))
            else:
                painter.drawLine(start_point, end_point)
                percent = (end_price / start_price - 1) * 100 if start_price else 0.0
                percent_text = f"{percent:+.2f}%"
                text_x = min(self.width() - right - 58, max(left, end_point.x() + 5))
                text_y = min(price_bottom - 4, max(top + 14, end_point.y() - 5))
                painter.drawText(int(text_x), int(text_y), percent_text)
        painter.restore()
        for annotation in annotations:
            shape, _start_time, start_price, _end_time, _end_price, *_extra = annotation
            if shape != "가로선":
                continue
            label_y = y(start_price)
            label_text = f"{start_price:,.0f}"
            label_rect = QRectF(self.width() - right + 3, label_y - 9, right - 6, 18)
            label_background = QColor(self._background); label_background.setAlpha(210)
            painter.fillRect(label_rect, label_background)
            painter.setPen(self._horizontal_line_color); painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter, label_text)
        painter.setPen(QPen(self._grid, 1))
        painter.drawLine(int(left), int(volume_top), int(self.width() - right), int(volume_top))
        painter.setPen(self._foreground)
        painter.drawText(int(self.width() - right + 5), int(volume_top + 10), f"최대 {format_trade_value_eok(max_trade_value, 0)}" if trade_value_available else "거래대금 자료 없음")
        if visible_threshold > 0 and trade_value_available:
            threshold_y = volume_bottom - visible_threshold / max_trade_value * max(1.0, volume_bottom - volume_top)
            threshold_color = QColor("#F57C00")
            painter.setPen(QPen(threshold_color, 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(int(left), int(threshold_y), int(self.width() - right), int(threshold_y))
            label = "일봉" if self._interval == "일봉" else "1분"
            label_y = threshold_y - 4 if abs(threshold_y - volume_top) >= 18 else threshold_y + 16
            painter.drawText(int(self.width() - right + 5), int(label_y), f"{label} {format_trade_value_eok(visible_threshold, 1)}")

        average_lines: list[tuple[str, float, float, QColor, str]] = []
        for key, fills, color, label in (() if self._reference_fills else (
            ("buy", tuple(fill for fill in day_fills if fill.side == "매수"), QColor(self._up_color), "평균 매수"),
            ("sell", tuple(fill for fill in day_fills if fill.side == "매도"), QColor(self._down_color), "평균 매도"),
        )):
            quantity = sum(fill.quantity for fill in fills)
            if quantity:
                average = sum(fill.quantity * fill.price for fill in fills) / quantity
                line_y = y(average); painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
                painter.drawLine(int(left), int(line_y), int(self.width() - right), int(line_y))
                average_lines.append((key, average, line_y - 3, color, label))
        if len(average_lines) == 2 and round(average_lines[0][1]) == round(average_lines[1][1]):
            common = (average_lines[0][1] + average_lines[1][1]) / 2
            painter.setPen(QColor("#7B1FA2"))
            painter.drawText(int(left + 4), int(min(value[2] for value in average_lines)), f"평균 매수·매도 {common:,.0f} · 보합")
        else:
            label_positions = average_price_label_positions(tuple((value[0], value[1], value[2]) for value in average_lines))
            for key, average, _line_y, color, label in average_lines:
                painter.setPen(color)
                painter.drawText(int(left + 4), int(label_positions[key]), f"{label} {average:,.0f}")

        callout_rects: list[QRectF] = []
        for fill in visible_fills:
            if self._reference_fills:
                continue
            index = fill_indices[trade_fill_key(fill)]
            x = int(left + (index + 0.5) * width)
            marker_y = int((price_bottom - 12) if fill.side == "매수" else (top + 12)) if self._reference_fills else int(y(float(fill.price)))
            color = QColor(self._up_color if fill.side == "매수" else self._down_color)
            candidate: QRectF | None = None
            callout_text = ""
            if self._show_trade_details:
                callout_text = trade_callout_text(fill)
                box_width = min(220.0, max(150.0, float(painter.fontMetrics().horizontalAdvance(callout_text) + 14)))
                box_height = 21.0
                box_x = max(left, min(x - box_width / 2, self.width() - right - box_width))
                direction = 1 if fill.side == "매수" else -1
                box_y = marker_y + 32 if direction > 0 else marker_y - 55
                box_y = max(top, min(box_y, price_bottom - box_height))
                candidate = QRectF(box_x, box_y, box_width, box_height)
                attempts = 0
                while any(candidate.adjusted(-2, -2, 2, 2).intersects(previous) for previous in callout_rects) and attempts < 20:
                    next_y = candidate.y() + direction * (box_height + 3)
                    if next_y < top or next_y + box_height > price_bottom:
                        next_x = candidate.x() + box_width * 0.35
                        if next_x + box_width > self.width() - right:
                            next_x = max(left, candidate.x() - box_width * 0.7)
                        candidate.moveTo(next_x, box_y)
                    else:
                        candidate.moveTop(next_y)
                    attempts += 1
                callout_rects.append(candidate)
                marker_back_y = marker_y + 24 if fill.side == "매수" else marker_y - 24
                painter.setPen(QPen(color, 1))
                anchor_y = candidate.top() if candidate.center().y() >= marker_y else candidate.bottom()
                painter.drawLine(QPoint(x, marker_back_y), QPoint(int(candidate.center().x()), int(anchor_y)))
            painter.setBrush(self._background); painter.setPen(QPen(color, 1.5))
            painter.drawPolygon(trade_marker_polygon(x, marker_y, fill.side))
            text_top = marker_y + 6 if fill.side == "매수" else marker_y - 24
            painter.setPen(color)
            painter.drawText(QRectF(x - 10, text_top, 20, 18), Qt.AlignmentFlag.AlignCenter, "B" if fill.side == "매수" else "S")
            if candidate is not None:
                # 설명이 봉이나 평균 가격 글씨 위에 놓이더라도 차트가 가려지지 않게
                # 배경은 옅게만 깐다. 글씨끼리의 충돌은 위의 배치 계산에서 피한다.
                background = QColor(self._background); background.setAlpha(75)
                painter.fillRect(candidate, background)
                painter.setPen(color)
                elided_callout = painter.fontMetrics().elidedText(callout_text, Qt.TextElideMode.ElideRight, int(candidate.width() - 4))
                painter.drawText(candidate.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, elided_callout)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(self._foreground)
        multiple_days = len({value.date() for value in row_minutes}) > 1
        if self._interval == "일봉":
            label_count = min(6, len(self._rows))
            label_indices = [round((len(self._rows) - 1) * step / max(1, label_count - 1)) for step in range(label_count)]
        else:
            label_indices = chart_time_tick_indices(row_minutes, self._interval, plot_width)
            if multiple_days:
                label_indices = multi_day_time_tick_indices(row_minutes, label_indices)
                painter.setPen(QPen(QColor("#E53935"), 1.5))
                for index in range(1, len(row_minutes)):
                    if row_minutes[index].date() == row_minutes[index - 1].date():
                        continue
                    boundary_x = left + index * width
                    painter.drawLine(int(boundary_x), int(top), int(boundary_x), int(volume_bottom))
                    painter.drawText(int(boundary_x + 4), self.height() - 5, row_minutes[index].strftime("%m-%d"))
                painter.setPen(self._foreground)
        for index in label_indices:
            x = left + (index + 0.5) * width
            label = row_minutes[index].strftime("%m-%d" if self._interval == "일봉" else "%H:%M")
            painter.drawText(int(x - 18), self.height() - 5, label)

        if 0 <= self._hover_index < len(self._rows):
            index = self._hover_index; row = self._rows[index]
            x = left + (index + 0.5) * width; close_y = y(float(row[4]))
            painter.setPen(QPen(self._foreground, 1, Qt.PenStyle.DotLine))
            painter.drawLine(int(x), int(top), int(x), int(volume_bottom))
            painter.drawLine(int(left), int(close_y), int(self.width() - right), int(close_y))
            time_text = row_minutes[index].strftime("%Y-%m-%d" if self._interval == "일봉" else "%Y-%m-%d %H:%M")
            info = (f"{time_text}  시 {int(row[1]):,}  고 {int(row[2]):,}  "
                    f"저 {int(row[3]):,}  종 {int(row[4]):,}  거래량 {int(row[5]):,}  거래대금 {format_trade_value_eok(row[6])}")
            box_width = min(self.width() - 12, 610)
            overlay = QColor(self._background); overlay.setAlpha(225)
            painter.fillRect(QRectF(6, 27, box_width, 20), overlay)
            painter.setPen(self._foreground); painter.drawText(10, 42, info)

    def mouseMoveEvent(self, event: object) -> None:
        if not self._rows or not hasattr(event, "position"):
            return
        if self._pan_drag_origin is not None:
            origin_x, origin_start = self._pan_drag_origin
            plot_width = max(1.0, self.width() - 14.0 - 72.0)
            bar_width = plot_width / max(1, len(self._rows))
            moved_bars = int(round((event.position().x() - origin_x) / bar_width))
            self.set_view_start(origin_start - moved_bars)
            if hasattr(event, "accept"):
                event.accept()
            return
        if self._drawing_start is not None:
            point = self._drawing_data_point(event.position())
            if point is not None:
                self._drawing_preview = point; self.update()
            return
        left, right = 14.0, 72.0
        width = max(1.0, (self.width() - left - right) / len(self._rows))
        self._hover_index = max(0, min(len(self._rows) - 1, int((event.position().x() - left) / width)))
        self.update()

    def mousePressEvent(self, event: object) -> None:
        if not hasattr(event, "position") or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event); return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        selected = self._find_annotation_at(event.position())
        if selected is not None:
            self._selected_annotation = selected
            if self._annotations[selected][0] == "텍스트":
                self._begin_text_edit(selected, event.position())
            self.update(); return
        if self._drawing_mode == "끄기":
            self._selected_annotation = None
            if self._maximum_view_start() > 0:
                self._pan_drag_origin = (float(event.position().x()), self._view_start)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            if hasattr(event, "accept"):
                event.accept()
            return
        point = self._drawing_data_point(event.position())
        if point is not None:
            if self._drawing_mode == "텍스트":
                self._annotations.append(("텍스트", point[0], point[1], point[0], point[1], ""))
                self._selected_annotation = len(self._annotations) - 1
                self._begin_text_edit(self._selected_annotation, event.position())
                return
            if self._drawing_mode in ("가로선", "세로선"):
                first_time = datetime.fromisoformat(str(self._rows[0][0]))
                last_time = datetime.fromisoformat(str(self._rows[-1][0]))
                shape = self._drawing_mode
                self._annotations.append((shape, point[0] if shape == "세로선" else first_time, point[1], last_time, point[1]))
                self._selected_annotation = len(self._annotations) - 1
                self.annotations_changed.emit(tuple(self._annotations)); self.update(); return
            self._drawing_start = point; self._drawing_preview = point; self.update()

    def mouseReleaseEvent(self, event: object) -> None:
        if self._pan_drag_origin is not None and event.button() == Qt.MouseButton.LeftButton:
            self._pan_drag_origin = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if hasattr(event, "accept"):
                event.accept()
            return
        if self._drawing_start is None or not hasattr(event, "position") or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event); return
        point = self._drawing_data_point(event.position())
        if point is not None and point != self._drawing_start:
            self._annotations.append((self._drawing_mode, *self._drawing_start, *point))
            self._selected_annotation = len(self._annotations) - 1
            self.annotations_changed.emit(tuple(self._annotations))
        self._drawing_start = None; self._drawing_preview = None; self.update()

    def _drawing_data_point(self, position: object) -> tuple[datetime, float] | None:
        if not self._rows or not hasattr(position, "x") or not hasattr(position, "y"):
            return None
        left, right, top, bottom = 14.0, 72.0, 50.0, 24.0
        price_bottom = max(top + 80.0, self.height() * 0.73)
        if position.x() < left or position.x() > self.width() - right or position.y() < top or position.y() > price_bottom:
            return None
        row_minutes = [datetime.fromisoformat(str(row[0])) for row in self._rows]
        plot_width = max(1.0, self.width() - left - right)
        bar_width = plot_width / len(self._rows)
        index = max(0, min(len(self._rows) - 1, int((position.x() - left) / bar_width)))
        visible_end = row_minutes[-1] + (timedelta(days=1) if self._interval == "일봉" else timedelta(minutes=1))
        visible_fills = visible_trade_fills(self._fills, row_minutes[0], visible_end)
        prices = [float(value) for row in self._rows for value in row[1:5]] + [float(fill.price) for fill in visible_fills]
        raw_low, raw_high = min(prices), max(prices)
        padding = max(1.0, (raw_high - raw_low) * 0.08)
        low, high, _step = rounded_price_grid(
            raw_low - padding, raw_high + padding,
            adaptive_price_grid_lines(len(self._rows), self.height()),
        )
        ratio = (position.y() - top) / max(1.0, price_bottom - top)
        return row_minutes[index], high - ratio * (high - low)

    def _find_annotation_at(self, position: object) -> int | None:
        if not self._annotations or not self._rows:
            return None
        left, right, top = 14.0, 72.0, 50.0
        price_bottom = max(top + 80.0, self.height() * 0.73)
        row_minutes = [datetime.fromisoformat(str(row[0])) for row in self._rows]
        plot_width = max(1.0, self.width() - left - right)
        bar_width = plot_width / len(self._rows)
        visible_end = row_minutes[-1] + (timedelta(days=1) if self._interval == "일봉" else timedelta(minutes=1))
        visible_fills = visible_trade_fills(self._fills, row_minutes[0], visible_end)
        prices = [float(value) for row in self._rows for value in row[1:5]] + [float(fill.price) for fill in visible_fills]
        raw_low, raw_high = min(prices), max(prices)
        padding = max(1.0, (raw_high - raw_low) * 0.08)
        low, high, _step = rounded_price_grid(
            raw_low - padding, raw_high + padding,
            adaptive_price_grid_lines(len(self._rows), self.height()),
        )

        def screen(moment: datetime, price: float) -> tuple[float, float]:
            index = min(range(len(row_minutes)), key=lambda candidate: abs((row_minutes[candidate] - moment).total_seconds()))
            x_value = left + (index + 0.5) * bar_width
            y_value = top + (high - price) / max(1.0, high - low) * max(1.0, price_bottom - top)
            return x_value, y_value

        px, py = float(position.x()), float(position.y())
        for index in range(len(self._annotations) - 1, -1, -1):
            shape, start_time, start_price, end_time, end_price, *extra = self._annotations[index]
            x1, y1 = screen(start_time, start_price); x2, y2 = screen(end_time, end_price)
            hit = False
            if shape == "텍스트":
                value = str(extra[0]) if extra else ""
                width = max(16.0, len(value) * self._text_size * 0.75)
                height = self._text_size * 1.8
                hit = x1 - 4 <= px <= x1 + width and y1 - height <= py <= y1 + 5
            elif shape == "가로선":
                hit = left <= px <= self.width() - right and abs(py - y1) <= 7
            elif shape == "세로선":
                hit = top <= py <= price_bottom and abs(px - x1) <= 7
            elif shape == "사각형":
                hit = QRectF(QPoint(int(x1), int(y1)), QPoint(int(x2), int(y2))).normalized().adjusted(-6, -6, 6, 6).contains(px, py)
            else:
                dx, dy = x2 - x1, y2 - y1
                length_squared = dx * dx + dy * dy
                ratio = 0.0 if length_squared == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_squared))
                nearest_x, nearest_y = x1 + ratio * dx, y1 + ratio * dy
                hit = math.hypot(px - nearest_x, py - nearest_y) <= 7
            if hit:
                return index
        return None

    def keyPressEvent(self, event: object) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._selected_annotation is not None:
            if 0 <= self._selected_annotation < len(self._annotations):
                del self._annotations[self._selected_annotation]
                self.annotations_changed.emit(tuple(self._annotations))
            self._selected_annotation = None; self.update(); event.accept(); return
        super().keyPressEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._hover_index = -1; self.update()

    def wheelEvent(self, event: object) -> None:
        if not self._all_rows or not hasattr(event, "angleDelta"):
            return
        delta = event.angleDelta().y()
        modifiers = event.modifiers() if hasattr(event, "modifiers") else Qt.KeyboardModifier.NoModifier
        if self._ctrl_wheel_zoom_enabled and modifiers & Qt.KeyboardModifier.ControlModifier:
            current = len(self._all_rows) if self._view_count <= 0 else self._view_count
            self.set_view_count(max(1, current - 1 if delta > 0 else current + 1))
            if hasattr(event, "accept"):
                event.accept()
            return
        if self._maximum_view_start() <= 0:
            return
        step = max(1, len(self._rows) // 8)
        self.set_view_start(self._view_start - step if delta > 0 else self._view_start + step)
        if hasattr(event, "accept"):
            event.accept()
