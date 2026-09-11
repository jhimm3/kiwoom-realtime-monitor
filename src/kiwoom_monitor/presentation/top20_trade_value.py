"""TOP20 거래대금 화면의 백그라운드 작업.

차트와 창도 이 모듈로 옮길 예정이며, 먼저 DB 보완 작업을 메인 화면에서
분리해 UI와 저장소의 직접 결합 범위를 줄인다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QPainter, QPen, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository


class Top20TradeValueChart(QWidget):
    """30초마다 갱신되는 TOP20 구성의 한 분 누적 거래대금을 표시한다."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(520, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._minute_text = "준비 중"
        self._status_text = "순위 결과를 다음 30초 구간부터 반영합니다."
        self._completed: list[tuple[datetime, float, float, float]] = []
        self._current_minute: datetime | None = None
        self._current_values = (0.0, 0.0, 0.0)
        self._display_mode = "minute"
        self._view_end: int | None = None
        self._bars_per_screen = 60

    def set_display_mode(self, mode: str) -> None:
        self._display_mode = mode
        self._view_end = None
        self.update()

    def reset_view(self) -> None:
        self._view_end = None
        self.update()

    def _visible_bars(self) -> list[tuple[datetime, float, float, float, bool]]:
        bars = [(*item, False) for item in self._completed]
        if self._current_minute is not None:
            bars.append((self._current_minute, *self._current_values, True))
        end = len(bars) if self._view_end is None else min(len(bars), self._view_end)
        return bars[max(0, end - self._bars_per_screen):end]

    def wheelEvent(self, event: object) -> None:
        if not hasattr(event, "angleDelta"):
            super().wheelEvent(event); return
        count = len(self._completed) + (1 if self._current_minute is not None else 0)
        if count <= self._bars_per_screen:
            event.accept(); return
        end = count if self._view_end is None else self._view_end
        step = max(1, self._bars_per_screen // 6)
        end = max(self._bars_per_screen, end - step) if event.angleDelta().y() > 0 else min(count, end + step)
        self._view_end = None if end >= count else end
        self.update(); event.accept()

    def mouseMoveEvent(self, event: object) -> None:
        bars = self._visible_bars()
        left, top, right, bottom = 12, 54, max(13, self.width() - 96), self.height() - 32
        if not bars or right <= left or not hasattr(event, "position"):
            QToolTip.hideText(); return
        point = event.position()
        if point.x() < left or point.x() > right or point.y() < top or point.y() > bottom:
            QToolTip.hideText(); return
        slot = (right - left) / len(bars)
        index = min(len(bars) - 1, max(0, int((point.x() - left) / slot)))
        minute, kospi, kosdaq, unknown, current = bars[index]
        total = kospi + kosdaq + unknown
        status = " · 진행 중" if current else ""
        tooltip = (
            f"<b>{minute.strftime('%Y-%m-%d' if self._display_mode == 'daily' else '%H:%M')}{status}</b><br>"
            f"<span style='color:#2563EB'>●</span> 코스피: {self._amount(kospi)}<br>"
            f"<span style='color:#F59E0B'>●</span> 코스닥: {self._amount(kosdaq)}<br>"
            f"<span style='color:#64748B'>●</span> 시장 미확인: {self._amount(unknown)}<br>"
            f"전체: <b>{self._amount(total)}</b>"
        )
        QToolTip.showText(event.globalPosition().toPoint(), tooltip, self)

    def leaveEvent(self, event: object) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)

    def set_data(
        self, current_minute: datetime | None, current_values: tuple[float, float, float],
        completed: list[tuple[datetime, float, float, float]], status_text: str,
    ) -> None:
        self._current_minute = current_minute
        self._minute_text = (
            current_minute.strftime("%H:%M") if current_minute else
            (completed[-1][0].strftime("%Y-%m-%d") if self._display_mode == "daily" and completed else "준비 중")
        )
        self._current_values = current_values
        self._completed = list(completed)
        self._status_text = status_text
        self.update()

    @staticmethod
    def _amount(value: float) -> str:
        if value >= 10_000:
            return f"{int(value // 10_000)}조 {int(value % 10_000):,}억"
        return f"{value:,.1f}억"

    def paintEvent(self, event: object) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#F8FAFC"))
        painter.setPen(QColor("#CBD5E1"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.setPen(QColor("#0F172A"))
        font = painter.font(); font.setBold(True); painter.setFont(font)
        period = {"daily": "일별", "5m": "5분", "60m": "60분"}.get(self._display_mode, "1분")
        painter.drawText(10, 20, f"TOP20 {period} 거래대금 지수 · {self._minute_text}")
        font.setBold(False); painter.setFont(font)
        painter.setPen(QColor("#2563EB"))
        current_total = sum(self._current_values)
        painter.drawText(10, 40, f"현재 {self._amount(current_total)}")
        legend_x = 165
        for label, color in (("코스피", "#2563EB"), ("코스닥", "#F59E0B"), ("시장 미확인", "#94A3B8")):
            painter.fillRect(legend_x, 29, 9, 9, QColor(color))
            painter.setPen(QColor("#64748B"))
            painter.drawText(legend_x + 13, 39, label)
            legend_x += 69 if label != "시장 미확인" else 96
        left, top, right, bottom = 12, 54, max(13, self.width() - 96), self.height() - 32
        bars = self._visible_bars()
        maximum = max([kospi + kosdaq + unknown for _, kospi, kosdaq, unknown, _ in bars] or [1.0])
        # 매매일지 차트의 가격축처럼 오른쪽에 거래대금 눈금을 표시한다.
        for step in range(5):
            ratio = step / 4
            y = bottom - int((bottom - top) * ratio)
            painter.setPen(QColor("#E2E8F0"))
            painter.drawLine(left, y, right, y)
            painter.setPen(QColor("#475569"))
            painter.drawText(right + 6, y + 4, self._amount(maximum * ratio))
        if bars:
            slot = max(3.0, (right - left) / len(bars))
            bar_width = max(2, int(slot * 0.72))
            colors = ("#2563EB", "#F59E0B", "#94A3B8")
            for index, (bar_minute, kospi, kosdaq, unknown, current) in enumerate(bars):
                x = left + int(index * slot + (slot - bar_width) / 2)
                y = bottom
                for value, color in zip((kospi, kosdaq, unknown), colors):
                    if value <= 0:
                        continue
                    height = max(1, int((bottom - top) * value / maximum))
                    painter.fillRect(x, y - height, bar_width, height, QColor(color))
                    y -= height
                if current:
                    painter.setPen(QColor("#DC2626"))
                    painter.drawRect(x - 1, y - 1, bar_width + 1, bottom - y + 1)
                if index == 0 or index == len(bars) - 1 or index % max(1, len(bars) // 6) == 0:
                    painter.setPen(QColor("#64748B"))
                    label_text = bar_minute.strftime("%m-%d" if self._display_mode == "daily" else "%H:%M")
                    painter.drawText(max(left, x - 8), bottom + 14, label_text)
            # 같은 거래일의 분 기록이 이어지지 않으면 앱 미실행·연결 공백을 표시한다.
            for index in range(1, len(bars)):
                previous_minute = bars[index - 1][0]
                current_minute = bars[index][0]
                delta_minutes = int((current_minute - previous_minute).total_seconds() // 60)
                expected_minutes = 5 if self._display_mode == "5m" else 60 if self._display_mode == "60m" else 1
                if previous_minute.date() != current_minute.date() or delta_minutes <= expected_minutes:
                    continue
                missing_minutes = delta_minutes - expected_minutes
                boundary_x = left + int(index * slot)
                painter.setPen(QPen(QColor("#DC2626"), 1, Qt.PenStyle.DashLine))
                painter.drawLine(boundary_x, top, boundary_x, bottom)
                painter.setPen(QColor("#B91C1C"))
                label = f"수집 중단 {missing_minutes}분"
                label_width = painter.fontMetrics().horizontalAdvance(label)
                label_x = max(left, min(right - label_width, boundary_x + 3))
                painter.drawText(label_x, top + 12, label)
        painter.setPen(QColor("#64748B"))
        painter.drawText(10, self.height() - 5, self._status_text)


class Top20TradeValueWindow(QMainWindow):
    dateRequested = Signal(object)
    modeRequested = Signal(str)
    statisticsRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window_settings = QSettings("KiwoomMonitor", "Top20TradeValueWindow")
        self._geometry_tracking_ready = False
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(250)
        self._geometry_save_timer.timeout.connect(self._save_window_geometry)
        self.setWindowTitle("TOP20 1분 거래대금 지수")
        self.resize(820, 420)
        self.chart = Top20TradeValueChart()
        content = QWidget(); layout = QVBoxLayout(content)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("보기"))
        self.mode_combo = QComboBox()
        for label, value in (("1분봉", "minute"), ("5분봉", "5m"), ("60분봉", "60m"), ("일봉", "daily")):
            self.mode_combo.addItem(label, value)
        self.mode_combo.currentIndexChanged.connect(lambda: self.modeRequested.emit(str(self.mode_combo.currentData())))
        controls.addWidget(self.mode_combo)
        controls.addWidget(QLabel("조회 날짜"))
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True); self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.dateChanged.connect(lambda _value: self._request_selected_date())
        controls.addWidget(self.date_edit)
        lookup = QPushButton("조회"); lookup.clicked.connect(self._request_selected_date)
        today = QPushButton("오늘"); today.clicked.connect(self._request_today)
        controls.addWidget(lookup); controls.addWidget(today); controls.addStretch(1)
        statistics = QPushButton("통계")
        statistics.clicked.connect(lambda: self.statisticsRequested.emit(7))
        controls.addWidget(statistics)
        controls.addWidget(QLabel("마우스 휠: 이전·최신 구간 이동"))
        layout.addLayout(controls); layout.addWidget(self.chart, 1)
        self.setCentralWidget(content)
        self._restore_window_geometry()

    def _save_window_geometry(self) -> None:
        geometry = self.normalGeometry() if self.isMaximized() or self.isFullScreen() else self.geometry()
        frame = self.frameGeometry()
        self._window_settings.setValue("window_x", frame.x())
        self._window_settings.setValue("window_y", frame.y())
        self._window_settings.setValue("window_width", geometry.width())
        self._window_settings.setValue("window_height", geometry.height())
        self._window_settings.sync()

    def _restore_window_geometry(self) -> None:
        try:
            x = int(self._window_settings.value("window_x"))
            y = int(self._window_settings.value("window_y"))
            width = int(self._window_settings.value("window_width"))
            height = int(self._window_settings.value("window_height"))
        except (TypeError, ValueError):
            return
        self.resize(max(360, width), max(220, height))
        self.move(x, y)
        self._keep_inside_available_screen()

    def _keep_inside_available_screen(self) -> None:
        frame = self.frameGeometry()
        screen = next(
            (candidate for candidate in QApplication.screens() if frame.intersects(candidate.availableGeometry())),
            None,
        )
        if screen is None:
            screen = self.parentWidget().screen() if self.parentWidget() is not None else QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.resize(min(self.width(), available.width()), min(self.height(), available.height()))
        frame = self.frameGeometry()
        self.move(
            max(available.left(), min(frame.left(), available.right() - frame.width() + 1)),
            max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)),
        )

    def moveEvent(self, event: object) -> None:
        super().moveEvent(event)
        if self._geometry_tracking_ready:
            self._geometry_save_timer.start()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._geometry_tracking_ready:
            self._geometry_save_timer.start()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._keep_inside_available_screen()
        self._geometry_tracking_ready = True

    def _request_selected_date(self) -> None:
        selected = self.date_edit.date()
        selected_date = date(selected.year(), selected.month(), selected.day())
        while selected_date.weekday() >= 5:
            selected_date -= timedelta(days=1)
        if selected_date != date(selected.year(), selected.month(), selected.day()):
            self.date_edit.blockSignals(True)
            self.date_edit.setDate(QDate(selected_date.year, selected_date.month, selected_date.day))
            self.date_edit.blockSignals(False)
        self.dateRequested.emit(selected_date)

    def _request_today(self) -> None:
        previous = self.date_edit.date()
        self.date_edit.setDate(QDate.currentDate())
        if self.date_edit.date() == previous:
            self._request_selected_date()

    def closeEvent(self, event: QCloseEvent) -> None:
        # 다시 열 때 직전 그래프를 그대로 볼 수 있도록 창만 숨긴다.
        self._geometry_save_timer.stop()
        self._save_window_geometry()
        event.ignore()
        self.hide()


class Top20MarketRepairWorker(QThread):
    """저장된 TOP20 지수의 시장 구분을 UI 스레드 밖에서 보완한다."""

    completed = Signal(int)
    failed = Signal(str)

    def __init__(self, repository: MinuteBarRepository) -> None:
        super().__init__()
        self._repository = repository

    def run(self) -> None:
        try:
            self.completed.emit(self._repository.repair_top20_market_splits())
        except Exception as error:
            self.failed.emit(str(error))
