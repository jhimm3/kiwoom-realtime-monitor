"""메인 실시간 표와 완전히 분리되어 실행되는 장중 매매일지."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
import math
import html
import re
import sqlite3
import time as monotonic_time
import zipfile
import traceback
from dataclasses import replace
from xml.etree import ElementTree
from datetime import date, datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QPoint, QRectF, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QCloseEvent, QIcon, QImage, QPainter, QPen, QPolygon, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QColorDialog, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QScrollArea, QScrollBar, QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget, QSpinBox,
    QMessageBox, QInputDialog,
)

from kiwoom_monitor.application.minute_chart_service import MinuteChartService
from kiwoom_monitor.application.market_index_chart_service import MarketIndexChartService
from kiwoom_monitor.application.trade_history_service import TradeFill, TradeHistoryService
from kiwoom_monitor.application.trade_journal_summary import (
    TradeEpisode, TradeReview, group_trade_episodes, split_trade_episode_cycles, trade_fill_key,
)
from kiwoom_monitor.application.trade_review_analysis import analyze_trade_episode
from kiwoom_monitor.application.trade_journal_statistics import summarize_periods
from kiwoom_monitor.application.trade_chart import DailyTradeChartService, aggregate_chart_rows
from kiwoom_monitor.application.trade_cost_service import DailyTradeCost, TradeCostService, allocate_episode_cost, estimate_episode_cost
from kiwoom_monitor.application.trade_setup_classification import (
    TRADE_SETUP_TYPES, classify_trade_setup, classify_trade_setup_cycles, lesson_unverifiable_for,
    normalize_trade_setup_type,
)
from kiwoom_monitor.application.personal_trade_rules import (
    applicable_lesson_text, extract_structured_trade_rules,
)
from kiwoom_monitor.application.strategy_pack import (
    STRATEGY_RESULT_MODES, StrategyPackManifest, default_strategy_pack, select_strategy_candidate,
)
from kiwoom_monitor.application.strategy_pack_extraction import (
    COMPARISONS, STRATEGY_METRICS, ExtractedStrategyDraft, RULE_CATEGORIES, StrategyRuleDraft,
    data_burden, draft_change_labels, extend_reviewed_draft, extract_strategy_draft,
)

MOVING_AVERAGE_DEFAULTS: dict[int, tuple[bool, str, float]] = {
    5: (True, "#EC407A", 1.5), 10: (True, "#1976D2", 1.5),
    20: (True, "#FBC02D", 1.5), 60: (True, "#8BC34A", 1.5),
    120: (True, "#424242", 1.5),
}

from kiwoom_monitor.application.generic_strategy_evaluator import evaluate_strategy_pack
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import news_at_execution
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.news_process import _application_icon_path, _parent_is_alive, _set_taskbar_app_id


def rounded_price_grid(minimum: float, maximum: float, target_lines: int = 8) -> tuple[float, float, float]:
    """현재 보이는 가격 범위를 거래소 호가 단위에 맞춰 촘촘하게 표시한다."""
    if maximum <= minimum:
        maximum = minimum + max(1.0, abs(minimum) * 0.01)
    raw_step = (maximum - minimum) / max(1, target_lines - 1)
    magnitude = 10 ** math.floor(math.log10(max(raw_step, 1.0)))
    step = next((factor * magnitude for factor in (1, 2, 5, 10) if factor * magnitude >= raw_step), 10 * magnitude)
    midpoint = (minimum + maximum) / 2
    # KRX 주권 가격대별 호가 단위. 축 간격은 이 단위의 정수배로만 만들되,
    # 예전처럼 9천 원대를 무조건 100원으로 강제하지 않는다. 따라서 화면
    # 봉 수를 줄이면 해당 구간의 작은 등락도 차트 높이를 충분히 사용한다.
    tick = 1 if midpoint < 2_000 else 5 if midpoint < 5_000 else 10 if midpoint < 20_000 else 50 if midpoint < 50_000 else 100 if midpoint < 200_000 else 500 if midpoint < 500_000 else 1_000
    step = max(float(tick), math.ceil(step / tick) * float(tick))
    lower = math.floor(minimum / step) * step
    upper = math.ceil(maximum / step) * step
    if upper <= lower:
        upper = lower + step
    return lower, upper, step


def adaptive_price_grid_lines(visible_bars: int, chart_height: int) -> int:
    """키움 차트처럼 확대 정도와 화면 높이에 따라 5~15개 눈금을 사용한다."""
    bars = max(1, int(visible_bars))
    zoom_target = round(15 - max(0, bars - 30) / 30)
    height_target = round(max(1, int(chart_height)) * 0.73 / 28)
    return max(5, min(15, zoom_target, height_target))


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


def visible_trade_fills(
    fills: tuple[TradeFill, ...], start: datetime, end: datetime,
) -> tuple[TradeFill, ...]:
    return tuple(fill for fill in fills if start <= fill.filled_at < end)


def chart_close_information(
    fills: tuple[TradeFill, ...], rows: tuple[tuple[object, ...], ...], *, index_mode: bool = False,
) -> str:
    """매매 마지막 날의 종가와 시가 대비 등락률을 만든다."""
    target_day = max((fill.filled_at.date() for fill in fills), default=None)
    day_rows = tuple(
        row for row in rows
        if target_day is not None and datetime.fromisoformat(str(row[0])).date() == target_day
    )
    if not day_rows:
        close_text = "당일 종가 미확정" if target_day == date.today() else "확정 일봉 보완 대기"
    else:
        close_price = float(day_rows[-1][4]) if index_mode else int(day_rows[-1][4])
        previous_rows = tuple(
            row for row in rows if datetime.fromisoformat(str(row[0])).date() < target_day
        )
        if previous_rows:
            previous_close = float(previous_rows[-1][4]) if index_mode else int(previous_rows[-1][4])
            previous_change = (close_price / previous_close - 1) * 100 if previous_close else 0.0
            price_text = f"{close_price:,.2f}" if index_mode else f"{close_price:,}원"
            close_text = f"{target_day:%m-%d} 종가 {price_text}  ·  전일 종가 대비 {previous_change:+.2f}%"
        else:
            price_text = f"{close_price:,.2f}" if index_mode else f"{close_price:,}원"
            close_text = f"{target_day:%m-%d} 종가 {price_text}  ·  전일 종가 대비 자료 없음"
    return close_text


def trade_callout_text(fill: TradeFill) -> str:
    return f"{fill.filled_at:%H:%M:%S} · {fill.price:,}원 · {fill.quantity:,}주"


def format_trade_value_eok(value: object, decimals: int = 2) -> str:
    """억원 단위 거래대금을 조 단위가 넘으면 '몇조 몇억'으로 읽기 쉽게 표시한다."""
    if value is None:
        return "자료 없음"
    amount = max(0.0, float(value))
    if amount < 10_000:
        return f"{amount:,.{decimals}f}억"
    jo = int(amount // 10_000)
    eok = int(round(amount - jo * 10_000))
    if eok >= 10_000:
        jo += 1; eok = 0
    return f"{jo:,}조" if eok == 0 else f"{jo:,}조 {eok:,}억"


def chart_time_step_minutes(row_count: int, interval: str, plot_width: float) -> int:
    """현재 확대 배율에 맞춰 둥근 시간 눈금 간격을 선택한다."""
    bar_minutes = {"1분": 1, "3분": 3, "5분": 5, "10분": 10, "30분": 30, "60분": 60}.get(interval, 1)
    target_labels = max(2, int(max(1.0, plot_width) // 90))
    raw_step = max(bar_minutes, row_count * bar_minutes / target_labels)
    return next((step for step in (5, 10, 30, 60, 120, 240) if step >= raw_step), 480)


def chart_time_tick_indices(row_minutes: list[datetime], interval: str, plot_width: float) -> list[int]:
    if not row_minutes:
        return []
    step = chart_time_step_minutes(len(row_minutes), interval, plot_width)
    indices = [
        index for index, value in enumerate(row_minutes)
        if (value.hour * 60 + value.minute) % step == 0
    ]
    # 날짜가 바뀌었는데 해당 날짜에 둥근 시각 봉이 없으면 첫 봉으로 날짜만 구분한다.
    for index, value in enumerate(row_minutes):
        if index == 0 or value.date() != row_minutes[index - 1].date():
            if not any(row_minutes[candidate].date() == value.date() for candidate in indices):
                indices.append(index)
    return sorted(set(indices))


def multi_day_time_tick_indices(row_minutes: list[datetime], indices: list[int]) -> list[int]:
    """여러 거래일 차트에서는 장 시작·마감 시각 대신 날짜 경계를 강조한다."""
    boundary_indices = {
        index for index in range(1, len(row_minutes))
        if row_minutes[index].date() != row_minutes[index - 1].date()
    }
    session_edges = {8 * 60, 9 * 60, 15 * 60 + 30, 20 * 60}
    return [
        index for index in indices
        if index not in boundary_indices
        and row_minutes[index].hour * 60 + row_minutes[index].minute not in session_edges
    ]


def _daily_chart_rows(rows: tuple[tuple[object, ...], ...]) -> tuple[tuple[object, ...], ...]:
    """저장된 지수 1분봉을 일별 OHLC로 묶어 일봉 선택에서도 표시한다."""
    grouped: dict[date, list[tuple[object, ...]]] = {}
    for row in rows:
        try:
            grouped.setdefault(datetime.fromisoformat(str(row[0])).date(), []).append(row)
        except (TypeError, ValueError):
            continue
    result: list[tuple[object, ...]] = []
    for day, values in sorted(grouped.items()):
        trade_values = [float(value[6]) for value in values if value[6] is not None]
        result.append((
            datetime.combine(day, time()).isoformat(timespec="minutes"),
            float(values[0][1]), max(float(value[2]) for value in values),
            min(float(value[3]) for value in values), float(values[-1][4]), 0,
            sum(trade_values) if trade_values else None, "market_index_daily",
        ))
    return tuple(result)


def average_price_label_positions(
    values: tuple[tuple[str, float, float], ...], minimum_gap: float = 14.0,
) -> dict[str, float]:
    """평균가가 높은 글자를 위에 두고 가까운 두 글자의 겹침을 해소한다."""
    positions: dict[str, float] = {}
    previous_y: float | None = None
    for key, _price, desired_y in sorted(values, key=lambda value: value[1], reverse=True):
        label_y = desired_y if previous_y is None else max(desired_y, previous_y + minimum_gap)
        positions[key] = label_y
        previous_y = label_y
    return positions


def load_personal_trade_rules(path: Path) -> tuple[str, ...]:
    """개인 문서의 전체 문단을 로컬 분석용으로 추출한다."""
    if not path.is_file():
        return ()
    try:
        if path.suffix.lower() == ".docx":
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            values = []
            for paragraph in root.iter(f"{namespace}p"):
                text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
                if text:
                    values.append(text)
        else:
            values = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return ()
    selected: list[str] = []
    for value in values:
        rule = value.strip().lstrip("-•*0123456789. ")
        if not rule:
            continue
        if rule not in selected:
            selected.append(rule)
    return tuple(selected[:2_000])


class JournalCellMarkerDelegate(QStyledItemDelegate):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent); self._selected_cell: tuple[int, int] | None = None; self.marker_enabled = True

    def set_selected_cell(self, cell: tuple[int, int] | None) -> None:
        self._selected_cell = cell

    def paint(self, painter: QPainter, option: object, index: object) -> None:
        active = self.marker_enabled and (
            self._selected_cell == (index.row(), index.column())
            or bool(option.state & QStyle.StateFlag.State_MouseOver)
        )
        cell_option = QStyleOptionViewItem(option)
        cell_option.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus)
        super().paint(painter, cell_option, index)
        if active:
            painter.save(); painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor("#0078D7"))
            painter.drawRoundedRect(option.rect.left() + 1, option.rect.center().y() - 7, 2, 14, 1, 1)
            painter.restore()


class StrategyPackExtractionWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, files: tuple[str, ...]) -> None:
        super().__init__(); self._files = files

    def run(self) -> None:
        try:
            self.completed.emit(extract_strategy_draft(tuple(Path(value) for value in self._files)))
        except Exception as error:
            self.failed.emit(str(error))


class StrategyRuleReviewDialog(QDialog):
    def __init__(self, pack: StrategyPackManifest, draft: ExtractedStrategyDraft,
                 parent: QWidget | None = None, previous: ExtractedStrategyDraft | None = None) -> None:
        super().__init__(parent); self.setWindowTitle(f"전략 규칙 검토 · {pack.name}"); self.resize(1100, 620)
        self._source_texts = draft.source_texts; layout = QVBoxLayout(self)
        summary = QLabel(f"추출 문단 {len(draft.rules)}개 · 원문 텍스트 {len(draft.source_texts)}개 파일을 로컬 매매일지 DB에 저장합니다.")
        layout.addWidget(summary)
        labels = draft_change_labels(previous, draft)
        self.table = QTableWidget(len(draft.rules), 10)
        self.table.setHorizontalHeaderLabels(("변경", "분류", "자동확인", "측정값", "비교", "기준값", "필요 데이터", "출처", "위치", "규칙 문장"))
        for row, rule in enumerate(draft.rules):
            category = QComboBox(); category.addItems(RULE_CATEGORIES); category.setCurrentText(rule.category)
            automatic = QCheckBox(); automatic.setChecked(rule.automatable)
            metric = QComboBox()
            for key, label in STRATEGY_METRICS.items(): metric.addItem(label, key)
            metric.setCurrentIndex(max(0, metric.findData(rule.metric_key)))
            comparison = QComboBox(); comparison.addItems(COMPARISONS); comparison.setCurrentText(rule.comparison)
            self.table.setItem(row, 0, QTableWidgetItem(labels[row]))
            self.table.setCellWidget(row, 1, category); self.table.setCellWidget(row, 2, automatic)
            self.table.setCellWidget(row, 3, metric); self.table.setCellWidget(row, 4, comparison)
            self.table.setItem(row, 5, QTableWidgetItem("" if rule.threshold is None else f"{rule.threshold:g}"))
            self.table.setItem(row, 6, QTableWidgetItem(", ".join(rule.required_data)))
            self.table.setItem(row, 7, QTableWidgetItem(rule.source_file)); self.table.setItem(row, 8, QTableWidgetItem(rule.location))
            self.table.setItem(row, 9, QTableWidgetItem(rule.text))
        self.table.setColumnWidth(3, 190); self.table.setColumnWidth(6, 150); self.table.setColumnWidth(9, 500)
        layout.addWidget(self.table)
        burden = QLabel("데이터 부담: " + " · ".join(f"{name}={state}" for name, state in data_burden(pack.required_data + pack.optional_data)))
        burden.setWordWrap(True); layout.addWidget(burden)
        note = QLabel("여기서는 추출 결과만 검토합니다. ‘자동확인’은 데이터로 검사할 가능성이 있다는 뜻이며, 아직 실제 판정식으로 승인된 것은 아닙니다.")
        note.setWordWrap(True); note.setStyleSheet("color:#666666;"); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("검토 내용 저장")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def draft(self) -> ExtractedStrategyDraft:
        rules = []
        for row in range(self.table.rowCount()):
            category = self.table.cellWidget(row, 1); automatic = self.table.cellWidget(row, 2)
            metric = self.table.cellWidget(row, 3); comparison = self.table.cellWidget(row, 4)
            raw_threshold = self.table.item(row, 5).text().strip()
            try: threshold = float(raw_threshold) if raw_threshold else None
            except ValueError: threshold = None
            rules.append(StrategyRuleDraft(
                category.currentText() if isinstance(category, QComboBox) else "설명",
                self.table.item(row, 9).text().strip(), self.table.item(row, 7).text(), self.table.item(row, 8).text(),
                tuple(item.strip() for item in self.table.item(row, 6).text().split(",") if item.strip()),
                automatic.isChecked() if isinstance(automatic, QCheckBox) else False,
                metric.currentData() if isinstance(metric, QComboBox) else "",
                comparison.currentText() if isinstance(comparison, QComboBox) else ">=", threshold,
            ))
        return ExtractedStrategyDraft(tuple(rule for rule in rules if rule.text), self._source_texts)


class StrategyPackDraftDialog(QDialog):
    """새 강의를 자동분석에 섞기 전 검토용 초안으로 등록한다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent); self.setWindowTitle("새 전략팩 초안"); self.resize(620, 330)
        layout = QVBoxLayout(self); form = QFormLayout()
        self.name = QLineEdit(); self.name.setPlaceholderText("예: ○○ 눌림매매 강의")
        self.setup_types = QComboBox(); self.setup_types.setEditable(True)
        self.setup_types.addItems(TRADE_SETUP_TYPES); self.setup_types.setCurrentText("돌파")
        self.setup_types.setToolTip("기본 유형을 선택하거나 여러 유형을 쉼표로 직접 입력할 수 있습니다.")
        self.required_data = QLineEdit(); self.required_data.setPlaceholderText("예: 1분봉, 일봉, VWAP, 거래량")
        self.optional_data = QLineEdit(); self.optional_data.setPlaceholderText("예: 뉴스, 수급, 호가")
        self.source_files = QLineEdit(); self.source_files.setReadOnly(True)
        choose = QPushButton("PDF·스크립트 선택"); choose.clicked.connect(self._choose_sources)
        source_row = QHBoxLayout(); source_row.addWidget(self.source_files); source_row.addWidget(choose)
        form.addRow("강의·전략 이름", self.name); form.addRow("주력 매매유형", self.setup_types)
        form.addRow("필수 데이터", self.required_data); form.addRow("선택 데이터", self.optional_data)
        form.addRow("강의 자료", source_row); layout.addLayout(form)
        notice = QLabel("등록한 자료는 아직 자동분석에 사용되지 않습니다. 다음 단계에서 추출된 규칙과 필요한 데이터를 검토한 뒤 활성화합니다.")
        notice.setWordWrap(True); notice.setStyleSheet("color:#666666;"); layout.addWidget(notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("검토 대기로 등록")
        buttons.accepted.connect(self._accept_if_valid); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _choose_sources(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self, "강의 PDF·스크립트 선택", "", "강의 자료 (*.pdf *.docx *.txt *.md);;모든 파일 (*)",
        )
        if selected:
            self.source_files.setText(" | ".join(selected))

    def _accept_if_valid(self) -> None:
        if not self.name.text().strip() or not self.source_files.text().strip():
            QMessageBox.information(self, "전략팩 초안", "전략 이름과 강의 자료를 선택해 주세요.")
            return
        self.accept()

    def manifest(self) -> StrategyPackManifest:
        name = self.name.text().strip()
        pack_id = "course_" + uuid.uuid5(uuid.NAMESPACE_URL, name + self.source_files.text()).hex[:12]
        split = lambda value: tuple(item.strip() for item in re.split(r"[,;/]", value) if item.strip())
        return StrategyPackManifest(
            pack_id=pack_id, name=name, version=1, enabled=False, priority=100,
            setup_types=split(self.setup_types.currentText()) or ("기타",),
            required_data=split(self.required_data.text()), optional_data=split(self.optional_data.text()),
            source_description="사용자가 등록한 강의 자료 · 규칙 검토 전",
            source_files=tuple(item.strip() for item in self.source_files.text().split("|") if item.strip()),
            review_status="draft",
        )


class StrategyPackManagerDialog(QDialog):
    def __init__(self, packs: tuple[StrategyPackManifest, ...], repository: JournalRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent); self.setWindowTitle("전략팩 관리"); self.resize(900, 430)
        self._packs = list(packs); self._repository = repository; self._worker: StrategyPackExtractionWorker | None = None
        self._pending_extension: tuple[int, StrategyPackManifest, ExtractedStrategyDraft | None, tuple[str, ...]] | None = None
        layout = QVBoxLayout(self)
        note = QLabel("검토 완료된 전략팩만 자동분석에 사용할 수 있습니다. 새 강의는 반드시 ‘검토 대기’로 등록됩니다.")
        note.setWordWrap(True); layout.addWidget(note)
        self.table = QTableWidget(0, 7); self.table.setHorizontalHeaderLabels(("사용", "전략팩", "상태", "우선순위", "매매유형", "필요 데이터", "자료"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); layout.addWidget(self.table)
        actions = QHBoxLayout(); add = QPushButton("새 전략팩"); extend = QPushButton("선택 전략팩에 강의 추가")
        extract = QPushButton("규칙 초안 만들기·보기"); approve = QPushButton("검증 후 승인")
        restore = QPushButton("이전 버전 복원"); remove = QPushButton("선택 초안 삭제")
        add.clicked.connect(self._add); extend.clicked.connect(self._add_sources); extract.clicked.connect(self._extract_or_review)
        approve.clicked.connect(self._approve); restore.clicked.connect(self._restore_version); remove.clicked.connect(self._remove)
        for button in (add, extend, extract, approve, restore, remove): actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        self.status = QLabel("대기"); self.status.setStyleSheet("color:#666666;"); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("전략팩 설정 반영")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self._render()

    def _render(self) -> None:
        self.table.setRowCount(len(self._packs))
        for row, pack in enumerate(self._packs):
            enabled = QTableWidgetItem(); enabled.setCheckState(Qt.CheckState.Checked if pack.enabled else Qt.CheckState.Unchecked)
            if pack.review_status != "approved":
                enabled.setFlags(enabled.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            name = QTableWidgetItem(f"{pack.name}  v{pack.version}"); name.setData(Qt.ItemDataRole.UserRole, pack.pack_id)
            priority = QSpinBox(); priority.setRange(1, 999); priority.setValue(pack.priority)
            status = {"approved": "사용 가능", "rules_reviewed": "규칙 검토 완료", "draft": "검토 대기"}.get(pack.review_status, pack.review_status)
            values = (enabled, name, QTableWidgetItem(status))
            for column, item in enumerate(values): self.table.setItem(row, column, item)
            self.table.setCellWidget(row, 3, priority)
            self.table.setItem(row, 4, QTableWidgetItem(", ".join(pack.setup_types)))
            self.table.setItem(row, 5, QTableWidgetItem(", ".join(pack.required_data)))
            self.table.setItem(row, 6, QTableWidgetItem(f"{len(pack.source_files)}개" if pack.source_files else pack.source_description))
        self.table.resizeColumnsToContents()

    def _add(self) -> None:
        dialog = StrategyPackDraftDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._packs.append(dialog.manifest()); self._render(); self.table.selectRow(len(self._packs) - 1)

    def _add_sources(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1" or self._worker is not None and self._worker.isRunning():
            return
        selected, _ = QFileDialog.getOpenFileNames(
            self, "추가 강의 PDF·스크립트 선택", "", "강의 자료 (*.pdf *.docx *.txt *.md);;모든 파일 (*)",
        )
        if not selected:
            return
        pack = self._packs[row]; previous = self._repository.load_strategy_pack_draft(pack.pack_id)
        additions = tuple(value for value in selected if value not in pack.source_files)
        if not additions:
            QMessageBox.information(self, "강의 추가", "이미 등록된 자료입니다."); return
        files = (*pack.source_files, *additions)
        missing = tuple(value for value in additions if not Path(value).is_file())
        if missing:
            QMessageBox.warning(self, "강의 추가", "강의 파일을 찾을 수 없습니다.\n" + "\n".join(missing)); return
        self._pending_extension = (row, pack, previous, files)
        self.status.setText(f"{pack.name} v{pack.version + 1} 변경 규칙을 비교하는 중…")
        self._worker = StrategyPackExtractionWorker(additions); self._worker.setParent(self)
        self._worker.completed.connect(lambda draft, target=row: self._extraction_completed(target, draft))
        self._worker.failed.connect(lambda message: self._extraction_failed(message)); self._worker.start()

    def _extract_or_review(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1":
            return
        pack = self._packs[row]
        saved = self._repository.load_strategy_pack_draft(pack.pack_id)
        if saved is not None:
            self._review(row, saved); return
        missing = tuple(value for value in pack.source_files if not Path(value).is_file())
        if missing:
            QMessageBox.warning(self, "전략팩 추출", "강의 파일을 찾을 수 없습니다.\n" + "\n".join(missing)); return
        self.status.setText(f"{pack.name} 텍스트와 규칙을 추출하는 중…")
        self._worker = StrategyPackExtractionWorker(pack.source_files); self._worker.setParent(self)
        self._worker.completed.connect(lambda draft, target=row: self._extraction_completed(target, draft))
        self._worker.failed.connect(lambda message: self._extraction_failed(message)); self._worker.start()

    def _extraction_completed(self, row: int, draft: object) -> None:
        if not isinstance(draft, ExtractedStrategyDraft): return
        previous = None
        if self._pending_extension is not None and self._pending_extension[0] == row:
            _, old_pack, previous, files = self._pending_extension
            self._repository.save_strategy_pack_version(old_pack, previous)
            draft = extend_reviewed_draft(previous, draft)
            self._packs[row] = StrategyPackManifest(**{
                **old_pack.to_dict(), "version": old_pack.version + 1, "source_files": files,
                "review_status": "draft", "enabled": False,
                "source_description": f"추가 강의 반영 · v{old_pack.version + 1} 재검토 필요",
            })
            self._pending_extension = None
        self._repository.save_strategy_pack_draft(self._packs[row].pack_id, draft)
        self.status.setText(f"추출 완료 · {len(draft.rules)}문단")
        self._render(); self.table.selectRow(row); self._review(row, draft, previous)

    def _extraction_failed(self, message: str) -> None:
        self._pending_extension = None
        self.status.setText("추출 실패"); QMessageBox.warning(self, "전략팩 추출", message)

    def _review(self, row: int, draft: ExtractedStrategyDraft, previous: ExtractedStrategyDraft | None = None) -> None:
        dialog = StrategyRuleReviewDialog(self._packs[row], draft, self, previous)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        reviewed = dialog.draft(); self._repository.save_strategy_pack_draft(self._packs[row].pack_id, reviewed)
        pack = self._packs[row]
        self._packs[row] = StrategyPackManifest(**{**pack.to_dict(), "review_status": "rules_reviewed", "enabled": False})
        self.status.setText(f"검토 저장 · {len(reviewed.rules)}문단"); self._render(); self.table.selectRow(row)

    def _restore_version(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1": return
        pack = self._packs[row]; versions = self._repository.load_strategy_pack_versions(pack.pack_id)
        if not versions:
            QMessageBox.information(self, "이전 버전 복원", "보관된 이전 버전이 없습니다."); return
        labels = [f"v{manifest.version} · {manifest.review_status} · 자료 {len(manifest.source_files)}개" for manifest, _ in versions]
        selected, accepted = QInputDialog.getItem(self, "이전 버전 복원", "복원할 버전", labels, 0, False)
        if not accepted: return
        index = labels.index(selected); old_manifest, old_draft = versions[index]
        if QMessageBox.question(self, "이전 버전 복원", f"{pack.name}을 v{old_manifest.version} 상태로 복원할까요?") != QMessageBox.StandardButton.Yes:
            return
        self._repository.save_strategy_pack_version(pack, self._repository.load_strategy_pack_draft(pack.pack_id))
        self._packs[row] = old_manifest
        if old_draft is not None: self._repository.save_strategy_pack_draft(pack.pack_id, old_draft)
        self.status.setText(f"v{old_manifest.version} 복원 완료"); self._render(); self.table.selectRow(row)

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1":
            return
        if self._packs[row].review_status == "approved":
            return
        removed = self._packs.pop(row); self._repository.delete_strategy_pack_draft(removed.pack_id); self._render()

    def _approve(self) -> None:
        row = self.table.currentRow()
        if row < 0 or self._packs[row].pack_id == "mimosa_v1": return
        pack = self._packs[row]; draft = self._repository.load_strategy_pack_draft(pack.pack_id)
        if draft is None:
            QMessageBox.information(self, "전략팩 승인", "먼저 규칙 초안을 만들고 검토해 주세요."); return
        automatic_entry = tuple(rule for rule in draft.rules if rule.category == "진입 조건" and rule.automatable)
        unmapped = tuple(rule for rule in automatic_entry if not rule.metric_key or rule.threshold is None)
        unsupported = tuple(name for name, state in data_burden(pack.required_data) if state == "지원 여부 검토 필요")
        realtime = tuple(name for name, state in data_burden(pack.required_data) if "승인 필요" in state)
        if not automatic_entry or unmapped or unsupported or realtime:
            details = []
            if not automatic_entry: details.append("자동 확인 가능한 진입 조건이 없습니다.")
            if unmapped: details.append(f"측정값·기준값 미연결 진입 조건 {len(unmapped)}개")
            if unsupported: details.append("지원 확인 필요 데이터: " + ", ".join(unsupported))
            if realtime: details.append("별도 실시간 저장 승인이 필요한 데이터: " + ", ".join(realtime))
            QMessageBox.warning(self, "전략팩 승인 불가", "\n".join(details)); return
        self._packs[row] = StrategyPackManifest(**{**pack.to_dict(), "review_status": "approved", "enabled": False})
        self.status.setText("승인 완료 · 사용하려면 사용 체크 후 설정을 저장하세요."); self._render(); self.table.selectRow(row)

    def packs(self) -> tuple[StrategyPackManifest, ...]:
        result = []
        for row, pack in enumerate(self._packs):
            enabled_item = self.table.item(row, 0); priority = self.table.cellWidget(row, 3)
            result.append(StrategyPackManifest(
                **{**pack.to_dict(),
                   "enabled": bool(enabled_item and enabled_item.checkState() == Qt.CheckState.Checked and pack.review_status == "approved"),
                   "priority": priority.value() if isinstance(priority, QSpinBox) else pack.priority}
            ))
        return tuple(result)


class JournalSettingsDialog(QDialog):
    def __init__(self, rules_path: str, estimated_buy_cost_rate: float, estimated_sell_cost_rate: float, auto_history_sync: bool, chart_background: str,
                 ctrl_wheel_zoom: bool, trade_value_threshold: float, daily_trade_value_threshold: float,
                 export_daily_chart: bool, export_minute_view_count: int, export_daily_separate: bool,
                 export_daily_view_count: int, show_trade_details: bool, drawing_line_width: float,
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
        self.export_daily_chart = QCheckBox("복기 이미지에 분봉·일봉 차트 함께 저장")
        self.export_daily_chart.setChecked(export_daily_chart)
        self.export_minute_view_count = QSpinBox(); self.export_minute_view_count.setRange(0, 2_000)
        self.export_minute_view_count.setSpecialValueText("현재 화면 봉 수"); self.export_minute_view_count.setSuffix("개")
        self.export_minute_view_count.setValue(export_minute_view_count)
        self.export_daily_separate = QCheckBox("일봉은 다른 봉 수 사용")
        self.export_daily_separate.setChecked(export_daily_separate)
        self.export_daily_view_count = QSpinBox(); self.export_daily_view_count.setRange(1, 2_000)
        self.export_daily_view_count.setSuffix("개"); self.export_daily_view_count.setValue(max(1, export_daily_view_count))
        self.export_daily_view_count.setEnabled(export_daily_separate)
        self.export_daily_separate.toggled.connect(self.export_daily_view_count.setEnabled)
        export_counts = QHBoxLayout(); export_counts.addWidget(QLabel("분봉")); export_counts.addWidget(self.export_minute_view_count)
        export_counts.addWidget(self.export_daily_separate); export_counts.addWidget(self.export_daily_view_count)
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
        self.export_daily_chart = QCheckBox("복기 이미지에 분봉·일봉 차트 함께 저장")
        self.export_daily_chart.setChecked(owner._export_daily_chart)
        self.export_minute_view_count = QSpinBox(); self.export_minute_view_count.setRange(0, 2_000)
        self.export_minute_view_count.setSpecialValueText("현재 화면 봉 수"); self.export_minute_view_count.setSuffix("개")
        self.export_minute_view_count.setValue(owner._export_minute_view_count)
        self.export_daily_separate = QCheckBox("일봉은 다른 봉 수 사용")
        self.export_daily_separate.setChecked(owner._export_daily_separate)
        self.export_daily_view_count = QSpinBox(); self.export_daily_view_count.setRange(1, 2_000)
        self.export_daily_view_count.setSuffix("개"); self.export_daily_view_count.setValue(max(1, owner._export_daily_view_count))
        self.export_daily_view_count.setEnabled(owner._export_daily_separate)
        self.export_daily_separate.toggled.connect(self.export_daily_view_count.setEnabled)
        export_counts = QHBoxLayout(); export_counts.addWidget(QLabel("분봉")); export_counts.addWidget(self.export_minute_view_count)
        export_counts.addWidget(self.export_daily_separate); export_counts.addWidget(self.export_daily_view_count)
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
        form.addRow("복기 이미지", self.export_daily_chart); form.addRow("이미지 저장 봉 수", export_counts)
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


class MinuteChart(QWidget):
    """외부 브라우저 없이 그리는 가벼운 장중 분봉 차트."""
    view_changed = Signal(int, int, int)
    view_count_changed = Signal(int)

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
        self._annotations.clear(); self._selected_annotation = None
        self._drawing_start = None; self._drawing_preview = None; self.update()

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
        self._view_count = max(0, int(count))
        visible_count = len(self._all_rows) if self._view_count <= 0 else self._view_count
        self._view_start = max(0, min(center - visible_count // 2, self._maximum_view_start()))
        self._apply_view()

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
        self._view_count = 0; self._view_start = 0; self._apply_view()

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
        self.view_count_changed.emit(self._view_count)
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
            self._selected_annotation = None; self.update(); return
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
                self._selected_annotation = len(self._annotations) - 1; self.update(); return
            self._drawing_start = point; self._drawing_preview = point; self.update()

    def mouseReleaseEvent(self, event: object) -> None:
        if self._drawing_start is None or not hasattr(event, "position") or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event); return
        point = self._drawing_data_point(event.position())
        if point is not None and point != self._drawing_start:
            self._annotations.append((self._drawing_mode, *self._drawing_start, *point))
            self._selected_annotation = len(self._annotations) - 1
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


class DetachedChartPanel(QWidget):
    """전용 창에서 봉 주기와 조작 상태를 독립적으로 가지는 차트 패널."""

    def __init__(self, panel_index: int, interval: str, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent); self._index = panel_index; self._settings = settings
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
        clear = QPushButton("그림 초기화"); panel_settings = QPushButton("⚙"); panel_settings.setToolTip(f"차트 {panel_index + 1} 설정")
        for widget in (QLabel("대상"), self.source, self.show_trades, self.show_details): source_actions.addWidget(widget)
        source_actions.addStretch(); layout.addLayout(source_actions)
        for widget in (QLabel("봉 주기"), self.interval, QLabel("화면 봉 수"), self.view_count, zoom_in, zoom_out, show_all, self.drawing, clear, panel_settings): chart_actions.addWidget(widget)
        chart_actions.addStretch(); layout.addLayout(chart_actions)
        self.chart = MinuteChart(); self.chart.setMinimumHeight(155); layout.addWidget(self.chart, 1)
        self.scroll = QScrollBar(Qt.Orientation.Horizontal); layout.addWidget(self.scroll)
        self.interval.currentTextChanged.connect(self.chart.set_interval)
        self.interval.currentTextChanged.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_interval", value))
        self.view_count.valueChanged.connect(self.chart.set_view_count)
        self.view_count.valueChanged.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_count", value))
        zoom_in.clicked.connect(lambda: self.chart.zoom(0.5)); zoom_out.clicked.connect(lambda: self.chart.zoom(2.0))
        show_all.clicked.connect(lambda: self.view_count.setValue(0)); clear.clicked.connect(self.chart.clear_annotations)
        panel_settings.clicked.connect(self._open_panel_settings)
        self.drawing.currentTextChanged.connect(lambda value: self.chart.set_drawing_mode(value.replace("그리기 ", "")))
        self.scroll.valueChanged.connect(self.chart.set_view_start)
        self.chart.view_changed.connect(self._sync_scroll)
        self.chart.view_count_changed.connect(self._sync_count)
        self.chart.set_interval(self.interval.currentText()); self.chart.set_view_count(initial_count)
        self.view_count.blockSignals(True); self.view_count.setValue(initial_count); self.view_count.blockSignals(False)
        self._episode: TradeEpisode | None = None; self._minute_rows: tuple[tuple[object, ...], ...] = ()
        self._daily_rows: tuple[tuple[object, ...], ...] = (); self._setup_types: tuple[str, ...] = ()
        self._index_rows: dict[str, tuple[tuple[object, ...], ...]] = {}
        self.source.currentTextChanged.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_source", value))
        self.source.currentTextChanged.connect(self._apply_source)
        self.show_trades.toggled.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_show_trades", value))
        self.show_trades.toggled.connect(self._apply_source)
        self.show_details.toggled.connect(lambda value: self._settings.setValue(f"detached_chart_{self._index}_show_details", value))
        self.show_details.toggled.connect(self.chart.set_trade_details_visible)

    def configure(self, background: str, grid: str, foreground: str, up: str, down: str, accent: str,
                  ctrl_zoom: bool, minute_threshold: float, daily_threshold: float, show_details: bool,
                  drawing_width: float, rectangle: str, line: str, horizontal: str, vertical: str) -> None:
        panel_background = self._settings.value(f"detached_chart_{self._index}_background", background, type=str)
        panel_accent = self._settings.value(f"detached_chart_{self._index}_accent", accent, type=str)
        panel_holding = self._settings.value(f"detached_chart_{self._index}_holding", "#FFEB96", type=str)
        self._last_configuration = (background, grid, foreground, up, down, accent, ctrl_zoom, minute_threshold,
                                    daily_threshold, show_details, drawing_width, rectangle, line, horizontal, vertical)
        self.chart.set_visual_style(panel_background, grid, foreground, up, down); self.chart.set_ctrl_wheel_zoom_enabled(ctrl_zoom)
        self.chart.set_holding_highlight_color(panel_holding)
        self._base_minute_threshold, self._base_daily_threshold = minute_threshold, daily_threshold
        self._apply_trade_value_thresholds()
        if not self._settings.contains(f"detached_chart_{self._index}_show_details"):
            self.show_details.setChecked(show_details)
        self.chart.set_trade_details_visible(self.show_details.isChecked())
        self.chart.set_drawing_style(drawing_width, rectangle, line, horizontal, vertical)
        self.chart.set_text_style(
            self._settings.value(f"detached_chart_{self._index}_text_color", "#263238", type=str),
            self._settings.value(f"detached_chart_{self._index}_text_size", 12, type=int),
        )
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

    def _apply_source(self, *_args: object) -> None:
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

    def _apply_trade_value_thresholds(self) -> None:
        source_key = {"코스피 지수": "kospi", "코스닥 지수": "kosdaq"}.get(self.source.currentText())
        if source_key:
            minute = float(self._settings.value(f"detached_{source_key}_minute_trade_value", 0.0))
            daily = float(self._settings.value(f"detached_{source_key}_daily_trade_value", 0.0))
        else:
            minute = getattr(self, "_base_minute_threshold", 0.0); daily = getattr(self, "_base_daily_threshold", 0.0)
        self.chart.set_trade_value_threshold(minute); self.chart.set_daily_trade_value_threshold(daily)

    def _open_panel_settings(self) -> None:
        fallback_background = self.chart._background.name()
        fallback_accent = getattr(self, "_accent_value", "#3F6FA0")
        fallback_holding = self.chart._holding_highlight_color.name()
        dialog = DetachedChartPanelSettingsDialog(
            self._settings, self._index, fallback_background, fallback_accent, fallback_holding, self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        dialog.save()
        if hasattr(self, "_last_configuration"):
            self.configure(*self._last_configuration)

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


class DetachedChartWindow(QMainWindow):
    """한 종목의 여러 주기 차트를 한 개 또는 여러 개 동시에 보는 창."""

    source_changed = Signal()

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
        # 전용 차트는 메인 차트와 독립된 색상 구성을 사용한다.
        detached_background = visual["background"] or background
        for index, panel in enumerate(self.panels):
            panel.configure(
                detached_background, visual["grid"], visual["foreground"], visual["up"], visual["down"], visual[f"accent_{index}"],
                ctrl_zoom, minute_threshold, daily_threshold, show_details,
                drawing_width, rectangle, line, horizontal, vertical,
            )

    def set_data(self, episode: TradeEpisode | None, minute_rows: tuple[tuple[object, ...], ...],
                 daily_rows: tuple[tuple[object, ...], ...], setup_types: tuple[str, ...],
                 index_rows: dict[str, tuple[tuple[object, ...], ...]] | None = None) -> None:
        name = (episode.summary.stock_name or episode.summary.stock_code) if episode is not None else "—"
        self.current_stock_label.setText(f"현재 종목: {name}")
        for panel in self.panels: panel.set_data(episode, minute_rows, daily_rows, setup_types, index_rows)

    def set_stock_choices(self, stocks: tuple[tuple[str, str], ...]) -> None:
        for panel in self.panels: panel.set_stock_choices(stocks)

    def set_trade_details_visible(self, visible: bool) -> None:
        for panel in self.panels: panel.chart.set_trade_details_visible(visible)

    def selected_stock_codes(self) -> tuple[str, ...]:
        return tuple(sorted({code for panel in self.panels if (code := panel.selected_stock_code())}))

    def selected_index_markets(self) -> tuple[str, ...]:
        labels = {"코스피 지수": "kospi", "코스닥 지수": "kosdaq"}
        return tuple(sorted({labels[panel.source.currentText()] for panel in self.panels if panel.source.currentText() in labels}))

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
        dialog = DetachedChartSettingsDialog(self._settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        dialog.save()
        parent = self.parent()
        if parent is not None and hasattr(parent, "_configure_detached_charts"):
            parent._configure_detached_charts()

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


class ConfirmWorker(QThread):
    completed = Signal(str, object, object, str)
    failed = Signal(str)

    def __init__(self, service: MinuteChartService, code: str, target_day: date | None = None, include_previous: bool = False) -> None:
        super().__init__()
        self._service, self._code, self._target_day, self._include_previous = service, code, target_day, include_previous

    def run(self) -> None:
        try:
            # 장중에는 연속조회하지 않는다. KRX/NXT 첫 페이지만으로 최근
            # 완료 봉을 확인해, 일지 창을 오래 열어도 TR 부담을 제한한다.
            now = datetime.now()
            target = datetime.combine(self._target_day, time()) if self._target_day else now
            after_close = target.date() < now.date() or now.hour >= 20
            bars = (
                self._service.load_two_trading_days(self._code, target)
                if self._include_previous else
                (self._service.load_today(self._code, target) if after_close else self._service.load_recent(self._code, target))
            )
            current = now.replace(second=0, microsecond=0)
            self.completed.emit(
                self._code, tuple(bar for bar in bars if bar.minute < current), now,
                "after_close_confirmed" if after_close else "api_confirmed",
            )
        except Exception as error:
            self.failed.emit(str(error))


class MarketIndexBackfillWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: MarketIndexChartService, day: date) -> None:
        super().__init__(); self._service, self._day = service, day

    def run(self) -> None:
        try:
            combined = {}; daily = {}
            for market in ("kospi", "kosdaq"):
                combined.update(self._service.load(market, datetime.combine(self._day, time())))
                daily[market] = self._service.load_daily(market, datetime.combine(self._day, time()))
            self.completed.emit((combined, daily))
        except Exception as error:
            self.failed.emit(str(error))


class HistoryWorker(QThread):
    completed = Signal(object, object, object)
    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, service: TradeHistoryService, cost_service: TradeCostService, start: date, end: date) -> None:
        super().__init__(); self._service, self._cost_service, self._start, self._end = service, cost_service, start, end

    def run(self) -> None:
        try:
            fills: list[TradeFill] = []
            day = self._end; requested = 0
            while day >= self._start:
                if self.isInterruptionRequested():
                    return
                if day.weekday() < 5:
                    requested += 1; self.progress.emit(f"과거 체결 조회 중 · {day:%Y-%m-%d}")
                    fills.extend(self._service.load_day(day))
                day -= timedelta(days=1)
            self.progress.emit("실제 수수료·세금 확인 중…")
            cost_error = ""
            try:
                costs = self._cost_service.load_period(self._start, self._end)
            except Exception as error:
                # 비용 API가 일시 실패해도 이미 받은 체결내역은 버리지 않는다.
                costs = ()
                cost_error = str(error)
            self.completed.emit((tuple(fills), costs, cost_error), self._start, self._end)
        except Exception as error:
            self.failed.emit(str(error))


class BackfillWorker(QThread):
    progress = Signal(str)
    item_completed = Signal(str, object, object)
    item_failed = Signal(str, object, str)
    completed = Signal(int, int)

    def __init__(self, service: MinuteChartService, tasks: tuple[tuple[str, date], ...]) -> None:
        super().__init__(); self._service, self._tasks = service, tasks

    def run(self) -> None:
        success = failed = 0
        total = len(self._tasks)
        for index, (code, day) in enumerate(self._tasks, 1):
            if self.isInterruptionRequested():
                break
            self.progress.emit(f"분봉 보완 {index}/{total} · {day:%Y-%m-%d} · {code}")
            try:
                bars = tuple(
                    bar for bar in self._service.load_today(code, datetime.combine(day, time()))
                    if bar.minute.date() == day
                )
                if not bars:
                    raise ValueError("해당 거래일의 분봉이 반환되지 않았습니다.")
                self.item_completed.emit(code, day, bars); success += 1
            except Exception as error:
                self.item_failed.emit(code, day, str(error)); failed += 1
        self.completed.emit(success, failed)


class DailyChartWorker(QThread):
    completed = Signal(str, object, str)
    failed = Signal(str)

    def __init__(self, service: DailyTradeChartService, code: str, day: date, target: str) -> None:
        super().__init__(); self._service, self._code, self._day, self._target = service, code, day, target

    def run(self) -> None:
        try:
            rows = self._service.load(self._code, datetime.combine(self._day, time()))
            self.completed.emit(self._code, rows, self._target)
        except Exception as error:
            self.failed.emit(str(error))


class JournalWindow(QMainWindow):
    _AUTO_BACKFILL_BATCH_SIZE = 20

    def __init__(self, monitor_db: Path, journal_db: Path, config: Path) -> None:
        super().__init__()
        self._monitor_db, self._config = monitor_db, config
        self._repo = JournalRepository(journal_db)
        try:
            self._snapshot_news_repo: StockNewsRepository | None = StockNewsRepository(monitor_db.parent / "news.sqlite3")
        except Exception:
            self._snapshot_news_repo = None
        self._journal_settings = QSettings("KiwoomMonitor", "TradingJournalWindow")
        self._selection_row_enabled = True; self._selection_marker_enabled = True; self._selection_row_color = "#DDEBF7"
        self._personal_rules_path = self._journal_settings.value("personal_rules_path", "", type=str)
        self._personal_rules = self._repo.load_personal_rules()
        self._structured_personal_rules = self._repo.load_structured_personal_rules()
        self._strategy_packs = self._repo.load_strategy_packs()
        self._active_strategy_pack = default_strategy_pack()
        self._strategy_result_mode = self._journal_settings.value("strategy_result_mode", "together", type=str)
        if self._strategy_result_mode not in STRATEGY_RESULT_MODES:
            self._strategy_result_mode = "together"
        if self._personal_rules_path and Path(self._personal_rules_path).is_file():
            # 문서가 다시 선택되거나 내용이 바뀌었을 때 앞부분 캐시가 남지 않도록
            # 시작할 때 전체 문단을 다시 추출한다. 원문 파일은 DB에 넣지 않는다.
            extracted_rules = load_personal_trade_rules(Path(self._personal_rules_path))
            if extracted_rules and extracted_rules != self._personal_rules:
                self._personal_rules = extracted_rules
                self._repo.save_personal_rules(extracted_rules)
            structured_rules = extract_structured_trade_rules(Path(self._personal_rules_path))
            if structured_rules and structured_rules != self._structured_personal_rules:
                self._structured_personal_rules = structured_rules
                self._repo.save_structured_personal_rules(structured_rules)
        # 저작권이 있는 강의 구조는 저장소·배포본에 포함하지 않고 사용자 data/private에만 둔다.
        # 존재할 때만 기존 개인원칙 문서보다 정교한 강의/주제 경계를 사용한다.
        private_rulebook = monitor_db.parent / "private" / "미모사_강의_구조화_v2.md"
        private_structured_rules = extract_structured_trade_rules(private_rulebook)
        if private_structured_rules and private_structured_rules != self._structured_personal_rules:
            self._structured_personal_rules = private_structured_rules
            self._repo.save_structured_personal_rules(private_structured_rules)
        self._auto_history_sync_enabled = self._journal_settings.value("auto_history_sync_enabled", True, type=bool)
        self._chart_background = self._journal_settings.value("chart_background", "#F3F3F3", type=str)
        self._ctrl_wheel_zoom_enabled = self._journal_settings.value("ctrl_wheel_zoom_enabled", True, type=bool)
        try:
            self._estimated_buy_cost_rate = float(self._journal_settings.value("estimated_buy_cost_rate", "0.015", type=str))
            self._estimated_sell_cost_rate = float(self._journal_settings.value("estimated_sell_cost_rate", "0.215", type=str))
        except ValueError:
            self._estimated_buy_cost_rate, self._estimated_sell_cost_rate = 0.015, 0.215
        try:
            self._trade_value_threshold = float(self._journal_settings.value("trade_value_threshold_eok", "40", type=str))
        except ValueError:
            self._trade_value_threshold = 40.0
        try:
            self._daily_trade_value_threshold = float(self._journal_settings.value("daily_trade_value_threshold_eok", "0", type=str))
        except ValueError:
            self._daily_trade_value_threshold = 0.0
        self._export_daily_chart = self._journal_settings.value("export_daily_chart", False, type=bool)
        self._export_minute_view_count = self._journal_settings.value("export_minute_view_count", 0, type=int)
        self._export_daily_separate = self._journal_settings.value("export_daily_separate", False, type=bool)
        self._export_daily_view_count = self._journal_settings.value("export_daily_view_count", 120, type=int)
        self._show_dual_history_chart = self._journal_settings.value("show_dual_history_chart", False, type=bool)
        self._detached_chart_window: DetachedChartWindow | None = None
        self._show_trade_details = self._journal_settings.value("show_trade_details", True, type=bool)
        try:
            self._drawing_line_width = float(self._journal_settings.value("drawing_line_width", "2", type=str))
        except ValueError:
            self._drawing_line_width = 2.0
        self._rectangle_color = self._journal_settings.value("rectangle_color", "#7B1FA2", type=str)
        self._line_color = self._journal_settings.value("line_color", "#7B1FA2", type=str)
        self._horizontal_line_color = self._journal_settings.value("horizontal_line_color", "#F57C00", type=str)
        self._vertical_line_color = self._journal_settings.value("vertical_line_color", "#00897B", type=str)
        self._moving_average_styles: dict[int, tuple[bool, str, float]] = {}
        for period, (default_enabled, default_color, default_width) in MOVING_AVERAGE_DEFAULTS.items():
            try:
                width = float(self._journal_settings.value(f"ma_{period}_width", str(default_width), type=str))
            except ValueError:
                width = default_width
            self._moving_average_styles[period] = (
                self._journal_settings.value(f"ma_{period}_enabled", default_enabled, type=bool),
                self._journal_settings.value(f"ma_{period}_color", default_color, type=str), width,
            )
        settings = LocalApiConfig(config).load()
        self._api_ready = bool(settings.app_key and settings.secret_key)
        client = KiwoomRestClient(settings)
        self._minute_service = MinuteChartService(client, include_nxt=True)
        self._market_index_service = MarketIndexChartService(client)
        self._market_index_worker: MarketIndexBackfillWorker | None = None
        self._market_index_requested_days: set[date] = set()
        self._compare_stock_requested: set[tuple[str, date]] = set()
        self._previous_chart_requested: set[tuple[str, date]] = set()
        self._daily_chart_service = DailyTradeChartService(client)
        self._history_service = TradeHistoryService(client)
        self._cost_service = TradeCostService(client)
        self._live_code = ""
        self._live_name = ""
        self._worker: ConfirmWorker | None = None
        self._history_worker: HistoryWorker | None = None
        self._quit_after_history_sync = False
        self._backfill_worker: BackfillWorker | None = None
        self._daily_chart_worker: DailyChartWorker | None = None
        self._pending_daily_chart: tuple[str, date, str] | None = None
        self._automatic_backfill_running = False
        self._automatic_backfill_attempted_for: tuple[date, bool] | None = None
        self._selected_history_day: date | None = None
        self._selected_history_code = ""
        self._selected_history_focus: datetime | None = None
        self._selected_history_range: tuple[date, date] | None = None
        self._visible_fills: tuple[TradeFill, ...] = ()
        self._visible_costs: tuple[DailyTradeCost, ...] = ()
        self._episodes: tuple[TradeEpisode, ...] = ()
        self._all_episodes: tuple[TradeEpisode, ...] = ()
        self._active_episode: TradeEpisode | None = None
        self._loading_review = False
        self._loading_setup = False
        self._last_confirmed_minute = ""
        self.setWindowTitle("키움 매매일지")
        self.resize(1040, 720)
        saved_geometry = QSettings("KiwoomMonitor", "TradingJournalWindow").value("geometry")
        if saved_geometry is not None:
            self.restoreGeometry(saved_geometry)
        root = QWidget(); layout = QVBoxLayout(root)
        self._status = QLabel("대기")
        self._tabs = QTabWidget(); layout.addWidget(self._tabs)

        history_page = QWidget(); history_layout = QVBoxLayout(history_page)
        filters = QHBoxLayout()
        self._from_date = QDateEdit(QDate.currentDate().addDays(-7)); self._from_date.setCalendarPopup(True)
        self._to_date = QDateEdit(QDate.currentDate()); self._to_date.setCalendarPopup(True)
        self._from_date.dateChanged.connect(self.reload_history); self._to_date.dateChanged.connect(self.reload_history)
        self._stock_filter = QLineEdit(); self._stock_filter.setPlaceholderText("종목명·코드 검색")
        self._stock_filter.setMaximumWidth(150); self._stock_filter.textChanged.connect(self._apply_history_filters)
        self._result_filter = QComboBox(); self._result_filter.addItems(("전체 손익", "수익", "손실", "보합"))
        self._result_filter.currentTextChanged.connect(self._apply_history_filters)
        self._review_filter = QComboBox(); self._review_filter.addItems(("전체 복기", "미작성", "작성 중", "복기 완료"))
        self._review_filter.currentTextChanged.connect(self._apply_history_filters)
        sync = QPushButton("키움에서 체결내역 가져오기"); sync.clicked.connect(self.sync_history)
        journal_settings = QPushButton("⚙"); journal_settings.setToolTip("매매일지 설정"); journal_settings.setFixedWidth(34)
        journal_settings.clicked.connect(self._open_journal_settings)
        filters.addWidget(QLabel("기간")); filters.addWidget(self._from_date); filters.addWidget(QLabel("~")); filters.addWidget(self._to_date)
        filters.addWidget(self._stock_filter); filters.addWidget(self._result_filter); filters.addWidget(self._review_filter)
        filters.addWidget(sync); filters.addStretch(); filters.addWidget(self._status); filters.addWidget(journal_settings)
        history_layout.addLayout(filters)
        self._period_summary = QLabel("체결내역을 가져오면 날짜·종목별 매매 결과가 표시됩니다.")
        self._period_summary.setStyleSheet("font-size: 14px; font-weight: 600; padding: 8px; background: #f4f7fb; border-radius: 4px;")
        export_review = QPushButton("복기 이미지 저장"); export_review.clicked.connect(self._export_review_image)
        summary_row = QHBoxLayout(); summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.addWidget(self._period_summary, 1); summary_row.addWidget(export_review)
        history_layout.addLayout(summary_row)
        group_actions = QHBoxLayout()
        split_group = QPushButton("선택 체결을 새 묶음으로 분리"); split_group.clicked.connect(self._split_selected_fills)
        merge_groups = QPushButton("선택한 매매 묶음 합치기"); merge_groups.clicked.connect(self._merge_selected_groups)
        reset_group = QPushButton("선택 묶음 자동분류로 복원"); reset_group.clicked.connect(self._reset_selected_groups)
        backfill = QPushButton("누락 분봉 일괄 보완"); backfill.clicked.connect(self._start_missing_backfill)
        retry_backfill = QPushButton("실패만 재시도"); retry_backfill.clicked.connect(lambda: self._start_missing_backfill(failed_only=True))
        stop_backfill = QPushButton("보완 중지"); stop_backfill.clicked.connect(self._stop_backfill)
        self._auto_backfill = QCheckBox("장 종료 후 자동 보완")
        self._auto_backfill.setChecked(QSettings("KiwoomMonitor", "TradingJournalWindow").value("auto_backfill", True, type=bool))
        self._auto_backfill.toggled.connect(self._save_auto_backfill_setting)
        group_actions.addWidget(split_group); group_actions.addWidget(merge_groups); group_actions.addWidget(reset_group); group_actions.addStretch()
        group_actions.addWidget(self._auto_backfill); group_actions.addWidget(backfill); group_actions.addWidget(retry_backfill); group_actions.addWidget(stop_backfill)
        history_layout.addLayout(group_actions)
        splitter = QSplitter(Qt.Orientation.Vertical); self._history_splitter = splitter
        self._summary_table = QTableWidget(0, 15)
        self._summary_table.setHorizontalHeaderLabels((
            "매매 기간", "종목", "매수", "매도", "매수금액", "매도금액",
            "추정 실현손익", "수익률", "실제 비용", "비용후 손익", "상태", "체결", "분봉", "복기", "매매유형",
        ))
        self._summary_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._summary_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._summary_table.itemSelectionChanged.connect(self._show_selected_summary)
        self._configure_journal_table(self._summary_table)
        splitter.addWidget(self._summary_table)
        self._fills_table = QTableWidget(0, 9)
        self._fills_table.setHorizontalHeaderLabels(("일시", "종목", "구분", "수량", "체결가", "체결금액", "주문유형", "시장", "주문번호"))
        self._fills_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._fills_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._fills_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._fills_table.itemSelectionChanged.connect(self._show_selected_fill)
        self._fills_table.cellClicked.connect(lambda row, _column: self._show_fill_at_row(row))
        self._configure_journal_table(self._fills_table)
        splitter.addWidget(self._fills_table)
        chart_box = QWidget(); chart_layout = QVBoxLayout(chart_box); chart_layout.setContentsMargins(0, 0, 0, 0); chart_layout.setSpacing(0)
        chart_actions = QHBoxLayout()
        self._history_interval = QComboBox(); self._history_interval.addItems(("1분", "3분", "5분", "10분", "30분", "60분", "일봉"))
        self._history_interval.currentTextChanged.connect(self._history_interval_changed)
        self._history_view_count = QSpinBox(); self._history_view_count.setRange(0, 2_000); self._history_view_count.setSingleStep(1)
        self._history_view_count.setSpecialValueText("전체"); self._history_view_count.setSuffix("개"); self._history_view_count.setValue(120)
        self._history_view_count.valueChanged.connect(lambda value: self._history_chart.set_view_count(value))
        chart_larger = QPushButton("확대"); chart_larger.clicked.connect(lambda: self._history_chart.zoom(0.5))
        chart_smaller = QPushButton("축소"); chart_smaller.clicked.connect(lambda: self._history_chart.zoom(2.0))
        chart_all = QPushButton("전체"); chart_all.clicked.connect(lambda: self._history_view_count.setValue(0))
        history_drawing = QComboBox(); history_drawing.addItems(("그리기 끄기", "선", "가로선", "세로선", "사각형", "텍스트"))
        history_drawing.currentTextChanged.connect(lambda value: self._history_chart.set_drawing_mode(value.replace("그리기 ", "")))
        clear_history_drawing = QPushButton("그림 초기화"); clear_history_drawing.clicked.connect(lambda: self._history_chart.clear_annotations())
        chart_settings = QPushButton("⚙"); chart_settings.setToolTip("차트 설정"); chart_settings.setFixedWidth(34)
        chart_settings.clicked.connect(self._open_chart_settings)
        self._history_trade_details = QCheckBox("체결정보")
        self._history_trade_details.setToolTip("기본 차트와 차트 따로보기의 현재 종목 차트에서 체결시간·가격·수량을 표시합니다.")
        self._history_trade_details.setChecked(self._show_trade_details)
        self._history_trade_details.toggled.connect(self._set_trade_details_visible)
        self._dual_history_chart = QCheckBox("분봉+일봉 같이 보기")
        self._dual_history_chart.setChecked(self._show_dual_history_chart)
        self._dual_history_chart.toggled.connect(self._toggle_dual_history_chart)
        detached_charts = QPushButton("차트 따로 보기"); detached_charts.clicked.connect(self._open_detached_charts)
        chart_actions.addWidget(QLabel("봉 주기")); chart_actions.addWidget(self._history_interval); chart_actions.addWidget(QLabel("화면 봉 수")); chart_actions.addWidget(self._history_view_count)
        chart_actions.addWidget(chart_larger); chart_actions.addWidget(chart_smaller); chart_actions.addWidget(chart_all)
        chart_actions.addWidget(history_drawing); chart_actions.addWidget(clear_history_drawing); chart_actions.addWidget(chart_settings); chart_actions.addStretch()
        chart_actions.addWidget(self._dual_history_chart); chart_actions.addWidget(self._history_trade_details)
        chart_actions.addWidget(detached_charts)
        chart_layout.addLayout(chart_actions)
        self._history_chart = MinuteChart(); self._history_chart.set_background(self._chart_background); self._history_chart.set_ctrl_wheel_zoom_enabled(self._ctrl_wheel_zoom_enabled); self._history_chart.set_trade_value_threshold(self._trade_value_threshold); self._history_chart.set_daily_trade_value_threshold(self._daily_trade_value_threshold); self._history_chart.set_trade_details_visible(self._show_trade_details); self._history_chart.set_drawing_style(self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color); self._history_chart.set_moving_average_styles(self._moving_average_styles); chart_layout.addWidget(self._history_chart)
        self._history_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self._history_scroll.setFixedHeight(12)
        self._history_scroll.valueChanged.connect(self._history_chart.set_view_start)
        self._history_chart.view_changed.connect(lambda maximum, current, page: self._sync_chart_scrollbar(self._history_scroll, maximum, current, page))
        self._history_chart.view_count_changed.connect(lambda count: self._sync_view_count_input(self._history_view_count, count))
        chart_layout.addWidget(self._history_scroll)
        self._history_daily_title = QLabel("일봉 차트")
        self._history_daily_title.setFixedHeight(18)
        self._history_daily_title.setStyleSheet("font-weight:600;color:#555555;background:#EEF1F4;border-top:1px solid #BFC6CF;padding-left:5px;")
        self._history_daily_chart = MinuteChart(); self._history_daily_chart.set_interval("일봉")
        self._history_daily_chart.set_top_margin(30)
        self._history_daily_chart.set_background(self._chart_background)
        self._history_daily_chart.set_ctrl_wheel_zoom_enabled(self._ctrl_wheel_zoom_enabled)
        self._history_daily_chart.set_daily_trade_value_threshold(self._daily_trade_value_threshold)
        self._history_daily_chart.set_trade_details_visible(self._show_trade_details)
        self._history_daily_chart.set_drawing_style(self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color)
        self._history_daily_chart.set_moving_average_styles(self._moving_average_styles)
        self._history_daily_title.setVisible(self._show_dual_history_chart)
        self._history_daily_chart.setVisible(self._show_dual_history_chart)
        chart_layout.addWidget(self._history_daily_title); chart_layout.addWidget(self._history_daily_chart)
        splitter.addWidget(chart_box)
        review_box = QGroupBox("선택한 매매 복기")
        review_form = QFormLayout(review_box)
        self._analysis_label = QTextEdit()
        self._analysis_label.setReadOnly(True)
        self._analysis_label.setPlainText("매매 묶음을 선택하면 분봉 기준 자동 분석이 표시됩니다.")
        self._analysis_label.setMinimumHeight(120)
        self._analysis_label.setMaximumHeight(220)
        self._analysis_label.setStyleSheet("padding: 7px; background: #f7f7f7; color: #333333;")
        self._analysis_data_status = QLabel("분석 자료: 매매 묶음을 선택하세요.")
        self._analysis_data_status.setWordWrap(True)
        self._analysis_data_status.setStyleSheet("padding: 4px 7px; color: #666666; background: #eef3f7;")
        setup_row = QVBoxLayout()
        self._setup_label = QTextEdit()
        self._setup_label.setReadOnly(True)
        self._setup_label.setPlainText("매매 묶음을 선택하면 진입 유형을 추정합니다.")
        self._setup_label.setMinimumHeight(58)
        self._setup_label.setMaximumHeight(100)
        self._setup_cycles = QTableWidget(0, 5)
        self._setup_cycles.setHorizontalHeaderLabels(("회차", "자동 유형", "세부 유형", "신뢰도", "직접 수정"))
        self._setup_cycles.verticalHeader().setVisible(False)
        self._setup_cycles.horizontalHeader().setStretchLastSection(True)
        self._setup_cycles.setMinimumHeight(68)
        self._setup_cycles.setMaximumHeight(260)
        setup_row.addWidget(self._setup_label)
        setup_row.addWidget(self._setup_cycles)
        self._review_reason = QTextEdit(); self._review_reason.setPlaceholderText("왜 진입했고 어떤 근거로 매도했는지 적어보세요."); self._review_reason.setMaximumHeight(70)
        self._review_note = QTextEdit(); self._review_note.setPlaceholderText("잘한 점, 놓친 점, 다음 매매에서 바꿀 점을 적어보세요."); self._review_note.setMaximumHeight(85)
        self._review_tags = QLineEdit(); self._review_tags.setPlaceholderText("예: 주도주 돌파, 테마주 돌파, 신규주 (쉼표로 구분)")
        review_options = QHBoxLayout()
        self._review_rating = QComboBox(); self._review_rating.addItems(("잘함", "보통", "아쉬움"))
        self._review_status = QComboBox(); self._review_status.addItems(("미작성", "작성 중", "복기 완료"))
        self._review_saved = QLabel("매매 묶음을 선택하세요.")
        save_review = QPushButton("지금 저장"); save_review.clicked.connect(self._save_active_review)
        self._journal_news_button = QPushButton("관련 뉴스 보기")
        self._journal_news_button.setEnabled(False)
        self._journal_news_button.clicked.connect(self._open_active_news)
        review_options.addWidget(QLabel("평가")); review_options.addWidget(self._review_rating)
        review_options.addWidget(QLabel("상태")); review_options.addWidget(self._review_status)
        review_options.addWidget(save_review); review_options.addWidget(self._journal_news_button); review_options.addWidget(self._review_saved); review_options.addStretch()
        review_form.addRow("예상 매매유형", setup_row)
        review_form.addRow("자동 분석", self._analysis_label)
        review_form.addRow("분석 자료", self._analysis_data_status)
        review_form.addRow("매매 이유", self._review_reason); review_form.addRow("복기 메모", self._review_note)
        review_form.addRow("태그", self._review_tags); review_form.addRow(review_options)
        review_scroll = QScrollArea()
        review_scroll.setWidgetResizable(True)
        review_scroll.setFrameShape(QFrame.Shape.NoFrame)
        review_scroll.setWidget(review_box)
        splitter.addWidget(review_scroll)
        splitter.setSizes((210, 170, 250, 210))
        saved_splitter = QSettings("KiwoomMonitor", "TradingJournalWindow").value("history_splitter")
        if saved_splitter is not None:
            splitter.restoreState(saved_splitter)
        history_layout.addWidget(splitter)
        help_text = QLabel("위 요약에서 날짜·종목을 선택하면 해당 체결만 아래에 표시되고, 분봉을 자동으로 보완해 매수·매도 위치를 차트에 표시합니다. 실현손익은 수수료·세금을 제외한 FIFO 추정값입니다.")
        help_text.setWordWrap(True); help_text.setStyleSheet("color: #666666;"); history_layout.addWidget(help_text)
        self._tabs.addTab(history_page, "과거 매매목록")

        # '오늘 진행 중' 탭은 화면에는 추가하지 않지만 set_stock(), 분봉 확인,
        # 차트 설정에서 내부 위젯을 계속 사용한다. 지역 변수로만 두면 __init__
        # 종료 후 QWidget과 자식 MinuteChart가 함께 삭제되므로 창 수명 동안
        # 명시적으로 보관한다.
        self._live_page = QWidget()
        live_page = self._live_page
        live_layout = QVBoxLayout(live_page)
        top = QHBoxLayout(); self._title = QLabel("메인 표에서 종목을 선택하면 오늘 분봉을 볼 수 있습니다.")
        self._live_interval = QComboBox(); self._live_interval.addItems(("1분", "3분", "5분", "10분", "30분", "60분", "일봉"))
        self._live_view_count = QSpinBox(); self._live_view_count.setRange(0, 2_000); self._live_view_count.setSingleStep(1)
        self._live_view_count.setSpecialValueText("전체"); self._live_view_count.setSuffix("개"); self._live_view_count.setValue(120)
        self._live_view_count.valueChanged.connect(lambda value: self._chart.set_view_count(value))
        live_zoom_in = QPushButton("확대"); live_zoom_in.clicked.connect(lambda: self._chart.zoom(0.5))
        live_zoom_out = QPushButton("축소"); live_zoom_out.clicked.connect(lambda: self._chart.zoom(2.0))
        live_all = QPushButton("전체"); live_all.clicked.connect(lambda: self._live_view_count.setValue(0))
        live_drawing = QComboBox(); live_drawing.addItems(("그리기 끄기", "선", "가로선", "세로선", "사각형", "텍스트"))
        live_drawing.currentTextChanged.connect(lambda value: self._chart.set_drawing_mode(value.replace("그리기 ", "")))
        clear_live_drawing = QPushButton("그림 초기화"); clear_live_drawing.clicked.connect(lambda: self._chart.clear_annotations())
        self._live_trade_details = QCheckBox("체결정보"); self._live_trade_details.setChecked(self._show_trade_details)
        self._live_trade_details.toggled.connect(self._set_trade_details_visible)
        confirm = QPushButton("완료 분봉 지금 확인"); confirm.clicked.connect(self.confirm_completed_bars)
        top.addWidget(self._title); top.addStretch(); top.addWidget(QLabel("봉 주기")); top.addWidget(self._live_interval)
        top.addWidget(QLabel("화면 봉 수")); top.addWidget(self._live_view_count)
        top.addWidget(live_zoom_in); top.addWidget(live_zoom_out); top.addWidget(live_all)
        top.addWidget(live_drawing); top.addWidget(clear_live_drawing); top.addWidget(self._live_trade_details); top.addWidget(confirm); live_layout.addLayout(top)
        self._chart = MinuteChart(); self._chart.set_background(self._chart_background); self._chart.set_ctrl_wheel_zoom_enabled(self._ctrl_wheel_zoom_enabled); self._chart.set_trade_value_threshold(self._trade_value_threshold); self._chart.set_daily_trade_value_threshold(self._daily_trade_value_threshold); self._chart.set_trade_details_visible(self._show_trade_details); self._chart.set_drawing_style(self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color); self._chart.set_moving_average_styles(self._moving_average_styles); live_layout.addWidget(self._chart)
        self._live_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self._live_scroll.valueChanged.connect(self._chart.set_view_start)
        self._chart.view_changed.connect(lambda maximum, current, page: self._sync_chart_scrollbar(self._live_scroll, maximum, current, page))
        self._chart.view_count_changed.connect(lambda count: self._sync_view_count_input(self._live_view_count, count))
        live_layout.addWidget(self._live_scroll)
        self._live_interval.currentTextChanged.connect(self._live_interval_changed)
        saved_interval = QSettings("KiwoomMonitor", "TradingJournalWindow").value("chart_interval", "1분", type=str)
        if saved_interval in ("1분", "3분", "5분", "10분", "30분", "60분", "일봉"):
            self._history_interval.setCurrentText(saved_interval); self._live_interval.setCurrentText(saved_interval)
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(("시간", "시가", "고가", "저가", "종가", "거래량", "거래대금(억)", "상태"))
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._configure_journal_table(self._table)
        live_layout.addWidget(self._table)
        note = QLabel("현재 진행 중인 봉은 ‘임시’, 키움에서 다시 받은 완료 봉은 ‘확인’으로 표시됩니다. 장 종료 후 다시 확인하면 같은 자리에 확정값이 덮어써집니다.")
        note.setWordWrap(True); live_layout.addWidget(note)
        # 실시간 순위표가 주 화면이므로 중복되는 '오늘 진행 중' 탭은 표시하지 않는다.

        backfill_page = QWidget(); backfill_layout = QVBoxLayout(backfill_page)
        backfill_actions = QHBoxLayout()
        self._backfill_filter = QComboBox(); self._backfill_filter.addItems(("전체", "미조회", "일부", "실패", "확정"))
        self._backfill_filter.currentTextChanged.connect(self._refresh_backfill_table)
        refresh_backfills = QPushButton("목록 새로고침"); refresh_backfills.clicked.connect(self._refresh_backfill_table)
        retry_selected = QPushButton("선택 항목 재조회"); retry_selected.clicked.connect(self._retry_selected_backfills)
        backfill_actions.addWidget(QLabel("상태")); backfill_actions.addWidget(self._backfill_filter)
        backfill_actions.addWidget(refresh_backfills); backfill_actions.addWidget(retry_selected); backfill_actions.addStretch()
        backfill_layout.addLayout(backfill_actions)
        self._backfill_table = QTableWidget(0, 5)
        self._backfill_table.setHorizontalHeaderLabels(("거래일", "종목코드", "상태", "분봉 수", "실패 사유"))
        self._backfill_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._backfill_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._backfill_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._backfill_table.horizontalHeader().setStretchLastSection(True)
        self._configure_journal_table(self._backfill_table)
        backfill_layout.addWidget(self._backfill_table)
        self._tabs.addTab(backfill_page, "분봉 보완 관리")

        stats_page = QWidget(); stats_layout = QVBoxLayout(stats_page)
        stats_actions = QHBoxLayout(); self._stats_unit = QComboBox(); self._stats_unit.addItems(("일간", "주간", "월간"))
        self._stats_unit.currentTextChanged.connect(self._refresh_statistics)
        self._stats_summary = QLabel(); self._stats_summary.setStyleSheet("font-size: 14px; font-weight: 600; padding: 8px; background: #f4f7fb;")
        stats_actions.addWidget(QLabel("집계")); stats_actions.addWidget(self._stats_unit); stats_actions.addStretch(); stats_layout.addLayout(stats_actions)
        stats_layout.addWidget(self._stats_summary)
        self._stats_table = QTableWidget(0, 7)
        self._stats_table.setHorizontalHeaderLabels(("기간", "매매", "수익", "손실", "승률", "실현손익", "수익률"))
        self._stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._configure_journal_table(self._stats_table)
        self._stats_table.horizontalHeader().setStretchLastSection(True); stats_layout.addWidget(self._stats_table)
        self._tabs.addTab(stats_page, "매매 통계")
        self.setCentralWidget(root)
        self._review_save_timer = QTimer(self); self._review_save_timer.setSingleShot(True)
        self._review_save_timer.timeout.connect(self._save_active_review)
        self._review_reason.textChanged.connect(self._review_changed); self._review_note.textChanged.connect(self._review_changed)
        self._review_tags.textChanged.connect(self._review_changed); self._review_rating.currentTextChanged.connect(self._review_changed)
        self._review_status.currentTextChanged.connect(self._review_changed)
        self._refresh_timer = QTimer(self); self._refresh_timer.timeout.connect(self.refresh); self._refresh_timer.start(1_000)
        self._confirm_timer = QTimer(self); self._confirm_timer.timeout.connect(self._confirm_on_new_minute); self._confirm_timer.start(1_000)
        self._auto_backfill_timer = QTimer(self); self._auto_backfill_timer.timeout.connect(self._check_automatic_backfill)
        self._auto_backfill_timer.start(60_000)
        self._history_sync_timer = QTimer(self); self._history_sync_timer.timeout.connect(self._auto_sync_history_once)
        self._history_sync_timer.start(600_000)
        self.reload_history()
        self._refresh_backfill_table()
        QTimer.singleShot(1_200, self._auto_sync_history_once)
        QTimer.singleShot(2_000, self._check_automatic_backfill)

    def _journal_tables(self) -> tuple[QTableWidget, ...]:
        names = ("_summary_table", "_fills_table", "_table", "_backfill_table", "_stats_table")
        return tuple(value for name in names if isinstance((value := getattr(self, name, None)), QTableWidget))

    def _configure_journal_table(self, table: QTableWidget) -> None:
        table.setMouseTracking(True); table.viewport().setMouseTracking(True)
        delegate = JournalCellMarkerDelegate(table); delegate.marker_enabled = self._selection_marker_enabled
        table.setItemDelegate(delegate)
        table.cellClicked.connect(lambda row, column, current=table: self._select_journal_cell(current, row, column))
        table.itemSelectionChanged.connect(lambda current=table: self._apply_journal_row_highlight(current))

    def _select_journal_cell(self, table: QTableWidget, row: int, column: int) -> None:
        delegate = table.itemDelegate()
        if isinstance(delegate, JournalCellMarkerDelegate):
            delegate.set_selected_cell((row, column))
        self._apply_journal_row_highlight(table); table.viewport().update()

    def _apply_journal_row_highlight(self, table: QTableWidget) -> None:
        selected_rows = {index.row() for index in table.selectionModel().selectedRows()}
        if table.selectionBehavior() != QTableWidget.SelectionBehavior.SelectRows and table.currentRow() >= 0:
            selected_rows.add(table.currentRow())
        for row in range(table.rowCount()):
            brush: QBrush | QColor = QColor(self._selection_row_color) if self._selection_row_enabled and row in selected_rows else QBrush()
            for column in range(table.columnCount()):
                item = table.item(row, column)
                if item is not None:
                    item.setBackground(brush)
        table.viewport().update()

    def _set_trade_details_visible(self, visible: bool) -> None:
        self._show_trade_details = bool(visible)
        self._journal_settings.setValue("show_trade_details", self._show_trade_details)
        # The old live-chart page is intentionally not added to the tab widget.
        # Qt deletes that orphan page (and its children), so touching its cached
        # checkbox/chart references here raises "Internal C++ object already
        # deleted" before the visible history chart can be updated.
        for name in ("_history_trade_details",):
            checkbox = getattr(self, name, None)
            if isinstance(checkbox, QCheckBox):
                try:
                    if checkbox.isChecked() != self._show_trade_details:
                        checkbox.blockSignals(True); checkbox.setChecked(self._show_trade_details); checkbox.blockSignals(False)
                except RuntimeError:
                    # Keep a stale optional widget from blocking the visible chart.
                    setattr(self, name, None)
        for name in ("_history_chart", "_history_daily_chart"):
            chart = getattr(self, name, None)
            if isinstance(chart, MinuteChart):
                try:
                    chart.set_trade_details_visible(self._show_trade_details)
                except RuntimeError:
                    setattr(self, name, None)
        detached = getattr(self, "_detached_chart_window", None)
        if isinstance(detached, DetachedChartWindow):
            detached.set_trade_details_visible(self._show_trade_details)

    def _open_journal_settings(self) -> None:
        dialog = JournalSettingsDialog(
            self._personal_rules_path, self._estimated_buy_cost_rate, self._estimated_sell_cost_rate, self._auto_history_sync_enabled,
            self._chart_background, self._ctrl_wheel_zoom_enabled, self._trade_value_threshold,
            self._daily_trade_value_threshold, self._export_daily_chart, self._export_minute_view_count,
            self._export_daily_separate, self._export_daily_view_count, self._show_trade_details,
            self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color,
            self._strategy_packs, self._strategy_result_mode, self._repo, self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._strategy_packs = dialog.strategy_packs
        self._repo.save_strategy_packs(self._strategy_packs)
        self._strategy_result_mode = str(dialog.strategy_result_mode.currentData() or "together")
        previous_rules_path = self._personal_rules_path
        self._personal_rules_path = dialog.rules_path.text().strip()
        if not self._personal_rules_path:
            self._personal_rules = ()
            self._structured_personal_rules = ()
            self._repo.save_personal_rules(())
            self._repo.save_structured_personal_rules(())
        elif self._personal_rules_path != previous_rules_path or Path(self._personal_rules_path).is_file():
            extracted_rules = load_personal_trade_rules(Path(self._personal_rules_path))
            if extracted_rules:
                self._personal_rules = extracted_rules
                self._repo.save_personal_rules(extracted_rules)
            structured_rules = extract_structured_trade_rules(Path(self._personal_rules_path))
            if structured_rules:
                self._structured_personal_rules = structured_rules
                self._repo.save_structured_personal_rules(structured_rules)
        self._estimated_buy_cost_rate = dialog.estimated_buy_cost_rate.value()
        self._estimated_sell_cost_rate = dialog.estimated_sell_cost_rate.value()
        self._auto_history_sync_enabled = dialog.auto_history_sync.isChecked()
        self._journal_settings.setValue("personal_rules_path", self._personal_rules_path)
        self._journal_settings.setValue("estimated_buy_cost_rate", str(self._estimated_buy_cost_rate))
        self._journal_settings.setValue("estimated_sell_cost_rate", str(self._estimated_sell_cost_rate))
        self._journal_settings.setValue("auto_history_sync_enabled", self._auto_history_sync_enabled)
        self._journal_settings.setValue("strategy_result_mode", self._strategy_result_mode)
        if self._auto_history_sync_enabled:
            QTimer.singleShot(0, self._auto_sync_history_once)
        self._render_summaries()
        if self._active_episode is not None:
            rows = self._load_selected_chart_rows()
            self._show_trade_analysis(self._active_episode, rows)

    def _open_chart_settings(self) -> None:
        dialog = JournalChartSettingsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._chart_background = dialog.chart_background.text().strip() or "#F3F3F3"
        self._ctrl_wheel_zoom_enabled = dialog.ctrl_wheel_zoom.isChecked()
        self._trade_value_threshold = dialog.trade_value_threshold.value()
        self._daily_trade_value_threshold = dialog.daily_trade_value_threshold.value()
        self._export_daily_chart = dialog.export_daily_chart.isChecked()
        self._export_minute_view_count = dialog.export_minute_view_count.value()
        self._export_daily_separate = dialog.export_daily_separate.isChecked()
        self._export_daily_view_count = dialog.export_daily_view_count.value()
        self._show_trade_details = dialog.show_trade_details.isChecked()
        self._drawing_line_width = dialog.drawing_line_width.value()
        self._rectangle_color = dialog.rectangle_color.text().strip() or "#7B1FA2"
        self._line_color = dialog.line_color.text().strip() or "#7B1FA2"
        self._horizontal_line_color = dialog.horizontal_line_color.text().strip() or "#F57C00"
        self._vertical_line_color = dialog.vertical_line_color.text().strip() or "#00897B"
        self._moving_average_styles = {
            period: (check.isChecked(), color.text().strip() or MOVING_AVERAGE_DEFAULTS[period][1], width.value())
            for period, (check, color, width) in dialog.moving_averages.items()
        }
        for key, value in (
            ("chart_background", self._chart_background), ("ctrl_wheel_zoom_enabled", self._ctrl_wheel_zoom_enabled),
            ("trade_value_threshold_eok", str(self._trade_value_threshold)),
            ("daily_trade_value_threshold_eok", str(self._daily_trade_value_threshold)),
            ("export_daily_chart", self._export_daily_chart), ("export_minute_view_count", self._export_minute_view_count),
            ("export_daily_separate", self._export_daily_separate), ("export_daily_view_count", self._export_daily_view_count),
            ("show_trade_details", self._show_trade_details), ("drawing_line_width", str(self._drawing_line_width)),
            ("rectangle_color", self._rectangle_color), ("line_color", self._line_color),
            ("horizontal_line_color", self._horizontal_line_color), ("vertical_line_color", self._vertical_line_color),
        ): self._journal_settings.setValue(key, value)
        for period, (enabled, color, width) in self._moving_average_styles.items():
            self._journal_settings.setValue(f"ma_{period}_enabled", enabled)
            self._journal_settings.setValue(f"ma_{period}_color", color)
            self._journal_settings.setValue(f"ma_{period}_width", str(width))
        for chart in (self._history_chart, self._history_daily_chart):
            chart.set_background(self._chart_background)
            chart.set_ctrl_wheel_zoom_enabled(self._ctrl_wheel_zoom_enabled)
            chart.set_trade_value_threshold(self._trade_value_threshold)
            chart.set_daily_trade_value_threshold(self._daily_trade_value_threshold)
            chart.set_drawing_style(self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color)
            chart.set_moving_average_styles(self._moving_average_styles)
        self._set_trade_details_visible(self._show_trade_details)
        if self._detached_chart_window is not None:
            self._configure_detached_charts(); self._sync_detached_charts()

    def _export_review_image(self) -> None:
        episode = self._active_episode
        if episode is None:
            self._status.setText("이미지로 저장할 매매 묶음을 먼저 선택하세요.")
            return
        summary = episode.summary
        desktop = Path.home() / "Desktop"
        base_dir = desktop if desktop.is_dir() else Path.home()
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", summary.stock_name or summary.stock_code)
        suggested = base_dir / f"{summary.trade_date:%Y%m%d}_{safe_name}_매매복기.png"
        selected, _ = QFileDialog.getSaveFileName(self, "매매 복기 이미지 저장", str(suggested), "PNG 이미지 (*.png)")
        if not selected:
            return
        output = Path(selected)
        if output.suffix.lower() != ".png":
            output = output.with_suffix(".png")

        # 화면 캡처를 확대하지 않고 차트 위젯을 큰 캔버스에 다시 그려 PNG 선명도를 확보한다.
        width, margin, gap = 2000, 44, 24
        content_width = width - margin * 2
        def render_chart(widget: MinuteChart) -> QImage:
            source_width = max(1, widget.width())
            source_height = max(1, widget.height())
            chart_height = max(520, int(source_height * content_width / source_width))
            rendered = QImage(content_width, chart_height, QImage.Format.Format_ARGB32)
            rendered.fill(widget._background)
            chart_painter = QPainter(rendered)
            chart_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            chart_painter.scale(content_width / source_width, chart_height / source_height)
            widget.render(chart_painter, QPoint())
            chart_painter.end()
            return rendered

        current_interval = self._history_interval.currentText()
        minute_interval = current_interval if current_interval != "일봉" else "1분"
        minute_count = self._export_minute_view_count or self._history_view_count.value()
        daily_count = self._export_daily_view_count if self._export_daily_separate else minute_count

        def export_chart(interval: str, daily_rows: tuple[tuple[object, ...], ...] = ()) -> MinuteChart:
            widget = MinuteChart(); widget.resize(self._history_chart.size())
            widget.set_background(self._chart_background)
            widget.set_trade_value_threshold(self._trade_value_threshold)
            widget.set_daily_trade_value_threshold(self._daily_trade_value_threshold)
            widget.set_trade_details_visible(self._show_trade_details)
            widget.set_drawing_style(self._drawing_line_width, self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color)
            widget.set_moving_average_styles(self._moving_average_styles)
            widget.set_fills(episode.fills); widget.set_setup_types(self._history_chart.setup_types())
            widget.set_rows(self._load_selected_chart_rows())
            if daily_rows: widget.set_daily_rows(daily_rows)
            widget.set_interval(interval)
            widget.set_view_count(daily_count if interval == "일봉" else minute_count)
            widget._annotations = list(self._history_chart._annotations)
            widget._annotation_fill_keys = self._history_chart._annotation_fill_keys
            focus = episode.fills[0].filled_at if episode.fills else episode.started_at
            widget.focus_time(focus)
            return widget

        chart = render_chart(self._history_chart)
        chart_title = current_interval
        daily_chart_image: QImage | None = None
        daily_missing = False
        if self._export_daily_chart:
            minute_chart = export_chart(minute_interval)
            chart = render_chart(minute_chart); chart_title = minute_interval
            daily_rows = self._repo.load_daily_bars(summary.stock_code, episode.ended_at.date())
            if daily_rows:
                daily_chart = export_chart("일봉", daily_rows)
                daily_chart_image = render_chart(daily_chart)
            else:
                daily_missing = True

        def document(title: str, body: str, *, prominent: bool = False) -> QTextDocument:
            value = QTextDocument()
            value.setDefaultStyleSheet(
                "body{font-family:'맑은 고딕','Malgun Gothic',sans-serif;font-size:24px;color:#222;line-height:1.35;}"
                "h1{font-size:40px;margin:0 0 12px 0;}h2{font-size:30px;margin:0 0 9px 0;color:#24496e;}"
                ".box{background:#f5f7fa;padding:18px;}"
            )
            heading = "h1" if prominent else "h2"
            escaped = html.escape(body or "—").replace("\n", "<br>")
            value.setHtml(f"<{heading}>{html.escape(title)}</{heading}><div class='box'>{escaped}</div>")
            value.setTextWidth(content_width)
            return value

        title_body = (
            f"{episode.started_at:%Y-%m-%d %H:%M} ~ {episode.ended_at:%Y-%m-%d %H:%M}\n"
            f"매수 {summary.buy_amount:,}원 · 매도 {summary.sell_amount:,}원 · "
            f"실현손익 {summary.realized_profit:+,}원 · 수익률 {summary.return_rate:+.2f}%"
        )
        review = self._repo.load_review(episode.group_id)
        review_body = (
            f"평가: {review.rating} · 상태: {review.status}\n"
            f"매매 이유: {review.reason or '미작성'}\n"
            f"복기 메모: {review.review or '미작성'}\n"
            f"태그: {review.tags or '없음'}"
        )
        title_document = document(summary.stock_name or summary.stock_code, title_body, prominent=True)
        chart_document = document(f"{chart_title} 차트", f"매수·매도 위치와 거래대금 · 저장 봉 수 {minute_count or '전체'}")
        daily_document = (
            document("일봉 차트", "저장된 일봉 데이터가 없어 이번 이미지에는 차트를 넣지 못했습니다.")
            if daily_missing else (document("일봉 차트", f"일봉 가격 흐름과 일별 거래대금 · 저장 봉 수 {daily_count or '전체'}") if daily_chart_image is not None else None)
        )
        detail_documents = (
            document("예상 매매유형", self._setup_label.toPlainText()),
            document("자동 분석", self._analysis_label.toPlainText()),
            document("직접 작성한 복기", review_body),
        )
        documents = tuple(value for value in (title_document, chart_document, daily_document, *detail_documents) if value is not None)
        document_heights = tuple(int(math.ceil(value.size().height())) for value in documents)
        total_height = margin + sum(value + gap for value in document_heights) + chart.height() + gap + margin
        if daily_chart_image is not None:
            total_height += daily_chart_image.height() + gap
        image = QImage(width, max(1, total_height), QImage.Format.Format_ARGB32)
        image.fill(QColor("#FFFFFF"))
        painter = QPainter(image); y = margin

        def draw_document(value: QTextDocument, height: int, top: int) -> int:
            # drawContents의 rect는 배치 좌표가 아니라 문서 내부 클립 영역이므로
            # painter 자체를 이동해야 두 번째 이후 문서가 잘리지 않는다.
            painter.save(); painter.translate(margin, top)
            value.drawContents(painter, QRectF(0, 0, content_width, height))
            painter.restore()
            return top + height + gap

        y = draw_document(title_document, int(math.ceil(title_document.size().height())), y)
        if daily_document is not None:
            y = draw_document(daily_document, int(math.ceil(daily_document.size().height())), y)
        if daily_chart_image is not None:
            painter.drawImage(margin, y, daily_chart_image); y += daily_chart_image.height() + gap
        y = draw_document(chart_document, int(math.ceil(chart_document.size().height())), y)
        painter.drawImage(margin, y, chart); y += chart.height() + gap
        detail_heights = tuple(int(math.ceil(value.size().height())) for value in detail_documents)
        for value, height in zip(detail_documents, detail_heights):
            y = draw_document(value, height, y)
        painter.end()
        if image.save(str(output), "PNG"):
            self._status.setText(f"복기 이미지 저장 완료 · {output.name}")
        else:
            self._status.setText("복기 이미지를 저장하지 못했습니다.")

    def set_stock(self, code: str, name: str) -> None:
        if not code:
            return
        if code != self._live_code:
            self._chart.clear_annotations()
        self._live_code, self._live_name = code, name
        self._chart.clear_daily_rows()
        now = datetime.now(); self._repo.remember_stock(code, name, now)
        self._title.setText(f"{name} ({code}) · 장중 매매일지")
        self.refresh()
        chart_rows = self._repo.load_chart_bars(code, now)
        if len({str(row[0])[:10] for row in chart_rows}) < 2 and (self._worker is None or not self._worker.isRunning()):
            self._start_bar_confirmation(code, date.today(), include_previous=True)
        if self._live_interval.currentText() == "일봉":
            self._start_daily_chart(code, date.today(), "live")

    def sync_history(self) -> None:
        if self._history_worker is not None and self._history_worker.isRunning():
            return
        start, end = self._from_date.date().toPython(), self._to_date.date().toPython()
        if start > end:
            start, end = end, start
        if (end - start).days > 31:
            self._status.setText("한 번에 최대 31일까지 조회할 수 있습니다."); return
        worker = HistoryWorker(self._history_service, self._cost_service, start, end)
        worker.progress.connect(self._status.setText); worker.failed.connect(lambda message: self._status.setText(f"조회 실패 · {message}"))
        worker.completed.connect(self._history_received); worker.finished.connect(self._history_finished); worker.finished.connect(worker.deleteLater)
        self._history_worker = worker; worker.start()

    def _auto_sync_history_once(self) -> bool:
        if not self._auto_history_sync_enabled or not self._api_ready:
            return False
        # NXT 애프터마켓 체결까지 포함하려면 당일 전체 체결은 20:05 이후에 확정한다.
        now = datetime.now()
        if now.time() < time(20, 5):
            return False
        if self._journal_settings.value("last_history_sync_day", "", type=str) == date.today().isoformat():
            return False
        self._status.setText("오늘 첫 실행 · 키움 체결내역 자동 확인 중…")
        self.sync_history()
        return self._history_worker is not None and self._history_worker.isRunning()

    def _history_received(self, result: object, start: object, end: object) -> None:
        if isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], tuple) and isinstance(result[1], tuple) and isinstance(start, date) and isinstance(end, date):
            fills, costs, cost_error = result
            self._repo.upsert_fills(fills); self._repo.upsert_trade_costs(costs)
            if datetime.now().time() >= time(20, 5):
                self._journal_settings.setValue("last_history_sync_day", date.today().isoformat())
            self._status.setText(
                f"체결 {len(fills)}건 저장 · 비용조회 실패(다음에 재시도)" if cost_error
                else f"조회 완료 · 체결 {len(fills)}건 · 실제비용 {len(costs)}묶음"
            )
            self.reload_history()

    def _history_finished(self) -> None:
        self._history_worker = None
        if self._quit_after_history_sync:
            self._quit_after_history_sync = False
            self.shutdown()
            app = QApplication.instance()
            if app is not None:
                app.setProperty("journal_terminating", True)
                app.exit(0)

    def reload_history(self) -> None:
        start = datetime.combine(self._from_date.date().toPython(), time())
        end = datetime.combine(self._to_date.date().toPython() + timedelta(days=1), time())
        fills = self._repo.load_fills_with_entry_context(start, end)
        self._visible_fills = fills
        self._visible_costs = self._repo.load_trade_costs(start - timedelta(days=365), end)
        episodes = group_trade_episodes(fills, self._repo.load_group_overrides())
        # 과거 체결은 검색 기간의 매도와 연결되는 진입을 복원하기 위해서만
        # 읽는다. 검색 기간과 무관한 과거 완결 매매는 목록에 섞지 않는다.
        self._all_episodes = tuple(
            episode for episode in episodes
            if any(start <= fill.filled_at < end for fill in episode.fills)
        )
        self._apply_history_filters()
        if not self._episodes:
            self._render_fills(fills)
        self._refresh_backfill_table()

    def _apply_history_filters(self, *args: object) -> None:
        query = self._stock_filter.text().strip().lower() if hasattr(self, "_stock_filter") else ""
        result_filter = self._result_filter.currentText() if hasattr(self, "_result_filter") else "전체 손익"
        review_filter = self._review_filter.currentText() if hasattr(self, "_review_filter") else "전체 복기"
        values: list[TradeEpisode] = []
        for episode in self._all_episodes:
            summary = episode.summary
            if query and query not in summary.stock_name.lower() and query not in summary.stock_code.lower():
                continue
            if result_filter == "수익" and summary.realized_profit <= 0:
                continue
            if result_filter == "손실" and summary.realized_profit >= 0:
                continue
            if result_filter == "보합" and summary.realized_profit != 0:
                continue
            if review_filter != "전체 복기" and self._repo.load_review(episode.group_id).status != review_filter:
                continue
            values.append(episode)
        self._episodes = tuple(values)
        self._render_summaries()
        self._refresh_statistics()

    def _render_summaries(self) -> None:
        selected_group_id = self._active_episode.group_id if self._active_episode is not None else ""
        self._summary_table.blockSignals(True)
        self._summary_table.clearSelection(); self._summary_table.setCurrentItem(None)
        delegate = self._summary_table.itemDelegate()
        if isinstance(delegate, JournalCellMarkerDelegate):
            delegate.set_selected_cell(None)
        self._summary_table.setRowCount(len(self._episodes))
        total_profit = sum(value.summary.realized_profit for value in self._episodes)
        cost_results = tuple(allocate_episode_cost(value, self._visible_fills, self._visible_costs) for value in self._episodes)
        projected_net = sum(
            value.net_realized_profit if value.net_realized_profit is not None
            else episode.summary.realized_profit - estimate_episode_cost(
                episode, self._estimated_buy_cost_rate, self._estimated_sell_cost_rate,
            )
            for episode, value in zip(self._episodes, cost_results)
        )
        actual_count = sum(1 for value in cost_results if value.net_realized_profit is not None)
        completed = tuple(value.summary for value in self._episodes if value.summary.matched_cost)
        wins = sum(1 for value in completed if value.realized_profit > 0)
        rate = wins / len(completed) * 100 if completed else 0.0
        selected_start = self._from_date.date().toPython()
        selected_end = self._to_date.date().toPython()
        days = len({
            fill.filled_at.date() for fill in self._visible_fills
            if selected_start <= fill.filled_at.date() <= selected_end
        })
        self._period_summary.setText(
            f"거래일 {days}일  ·  매매 묶음 {len(self._episodes)}건  ·  "
            f"승률 {rate:.1f}%  ·  추정 실현손익 예정 {total_profit:+,}원  ·  "
            f"비용 반영 예정 {projected_net:+,}원(실제 비용 {actual_count}/{len(self._episodes)}건)"
        )
        for row, episode in enumerate(self._episodes):
            summary = episode.summary
            saved_setup = self._repo.load_trade_setup(episode.group_id)
            setup_display = (saved_setup[1] or saved_setup[0].setup_type) if saved_setup else "분석 전"
            cycle_overrides = self._repo.load_trade_setup_cycle_overrides(episode.group_id)
            if saved_setup and cycle_overrides:
                automatic_types = tuple(
                    re.sub(r"^\d+차\s+", "", value)
                    for value in saved_setup[0].setup_type.split(" · ")
                )
                selected = tuple(cycle_overrides.get(index, value) for index, value in enumerate(automatic_types))
                setup_display = " · ".join(
                    f"{index + 1}차 {value}" if len(selected) > 1 else value
                    for index, value in enumerate(selected)
                )
            cost = allocate_episode_cost(episode, self._visible_fills, self._visible_costs)
            estimated_cost = estimate_episode_cost(
                episode, self._estimated_buy_cost_rate, self._estimated_sell_cost_rate,
            )
            cost_display = (
                f"{cost.total_cost:,}원" + (" · 배분" if cost.allocated else "")
                if cost.complete else
                (f"예정 {estimated_cost:,}원 · 매수 {self._estimated_buy_cost_rate:g}% / 매도 {self._estimated_sell_cost_rate:g}%" if estimated_cost else "정산 대기")
            )
            net_display = (
                f"{cost.net_realized_profit:+,}원" if cost.net_realized_profit is not None else
                (f"예정 {summary.realized_profit - estimated_cost:+,}원" if estimated_cost else "—")
            )
            period = episode.started_at.strftime("%Y-%m-%d %H:%M")
            if episode.ended_at != episode.started_at:
                period += " ~ " + episode.ended_at.strftime("%m-%d %H:%M")
            values = (
                period, summary.stock_name or summary.stock_code,
                f"{summary.buy_quantity:,}주", f"{summary.sell_quantity:,}주",
                f"{summary.buy_amount:,}원", f"{summary.sell_amount:,}원",
                f"{summary.realized_profit:+,}원", f"{summary.return_rate:+.2f}%",
                cost_display, net_display,
                ("수동 · " if episode.source == "manual" else "") + summary.state, f"{summary.fill_count}건",
                self._episode_bar_state(episode),
                self._repo.load_review(episode.group_id).status, setup_display,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, episode)
                if column in (6, 7, 9):
                    item.setForeground(QBrush(QColor("#D32F2F" if summary.realized_profit > 0 else ("#1976D2" if summary.realized_profit < 0 else "#555555"))))
                self._summary_table.setItem(row, column, item)
        self._summary_table.blockSignals(False)
        self._summary_table.resizeColumnsToContents()
        if self._episodes:
            selected_row = next(
                (row for row, episode in enumerate(self._episodes) if episode.group_id == selected_group_id),
                0,
            )
            self._summary_table.blockSignals(True)
            self._summary_table.selectRow(selected_row); self._summary_table.setCurrentCell(selected_row, 0)
            self._summary_table.blockSignals(False)
            self._select_journal_cell(self._summary_table, selected_row, 0)
            # selectRow()가 currentCell 설정 전에 selectionChanged를 내보내면 분석 호출이
            # 빈 선택으로 끝날 수 있으므로, 최종 선택이 완성된 뒤 명시적으로 갱신한다.
            self._show_selected_summary()

    def _render_fills(self, fills: tuple[TradeFill, ...]) -> None:
        self._fills_table.blockSignals(True)
        self._fills_table.clearSelection(); self._fills_table.setCurrentItem(None)
        delegate = self._fills_table.itemDelegate()
        if isinstance(delegate, JournalCellMarkerDelegate):
            delegate.set_selected_cell(None)
        self._fills_table.setRowCount(len(fills))
        for row, fill in enumerate(fills):
            amount = fill.quantity * fill.price
            values = (fill.filled_at.strftime("%Y-%m-%d %H:%M:%S"), fill.stock_name or fill.stock_code, fill.side,
                      f"{fill.quantity:,}", f"{fill.price:,}", f"{amount:,}", fill.order_type, fill.market, fill.order_no)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, fill)
                self._fills_table.setItem(row, column, item)
        self._fills_table.blockSignals(False)
        self._fills_table.resizeColumnsToContents()
        if fills:
            self._fills_table.selectRow(0); self._fills_table.setCurrentCell(0, 0)
            self._select_journal_cell(self._fills_table, 0, 0)

    def _episode_bar_state(self, episode: TradeEpisode) -> str:
        tasks = sorted({(fill.stock_code, fill.filled_at.date()) for fill in episode.fills}, key=lambda value: value[1])
        states = tuple(self._repo.bar_backfill_state(code, day) for code, day in tasks)
        if states and all(value.state == "확정" for value in states):
            return "확정"
        failed = sum(1 for value in states if value.state == "실패")
        confirmed = sum(1 for value in states if value.state == "확정")
        if failed:
            return f"실패 {failed}건"
        if confirmed:
            return f"일부 {confirmed}/{len(states)}"
        if any(value.state == "일부" for value in states):
            return "일부"
        return "미조회"

    def _backfill_tasks(self, *, failed_only: bool = False, all_saved: bool = False) -> tuple[tuple[str, date], ...]:
        cutoff = date.today() if datetime.now().hour < 20 else date.today() + timedelta(days=1)
        candidates = (
            self._repo.bar_backfill_candidates(cutoff) if all_saved else
            tuple(sorted({(fill.stock_code, fill.filled_at.date()) for fill in self._visible_fills if fill.filled_at.date() < cutoff}, key=lambda value: (value[1], value[0])))
        )
        tasks: list[tuple[str, date]] = []
        for code, day in candidates:
            state = self._repo.bar_backfill_state(code, day)
            if state.state == "확정":
                continue
            if failed_only and state.state != "실패":
                continue
            tasks.append((code, day))
        return tuple(tasks)

    def _start_missing_backfill(
        self, *, failed_only: bool = False, tasks: tuple[tuple[str, date], ...] | None = None,
        automatic: bool = False,
    ) -> None:
        if self._backfill_worker is not None and self._backfill_worker.isRunning():
            self._status.setText("분봉 보완이 이미 진행 중입니다."); return
        if self._history_worker is not None and self._history_worker.isRunning():
            self._status.setText("체결내역 조회가 끝난 뒤 분봉을 보완하세요."); return
        tasks = tasks if tasks is not None else self._backfill_tasks(failed_only=failed_only)
        if not tasks:
            self._status.setText("재시도할 실패 분봉이 없습니다." if failed_only else "보완할 누락 분봉이 없습니다."); return
        worker = BackfillWorker(self._minute_service, tasks)
        worker.progress.connect(self._status.setText)
        worker.item_completed.connect(self._backfill_received)
        worker.item_failed.connect(self._backfill_failed)
        worker.completed.connect(self._backfill_completed)
        worker.finished.connect(self._backfill_finished); worker.finished.connect(worker.deleteLater)
        self._automatic_backfill_running = automatic
        self._backfill_worker = worker; worker.start()

    def _save_auto_backfill_setting(self, enabled: bool) -> None:
        QSettings("KiwoomMonitor", "TradingJournalWindow").setValue("auto_backfill", enabled)
        if enabled:
            self._automatic_backfill_attempted_for = None
            self._check_automatic_backfill()

    def _check_automatic_backfill(self) -> None:
        if not self._auto_backfill.isChecked() or self._backfill_worker is not None or self._history_worker is not None:
            return
        today = date.today()
        attempt_key = (today, datetime.now().hour >= 20)
        if self._automatic_backfill_attempted_for == attempt_key:
            return
        tasks = self._backfill_tasks(all_saved=True)
        self._automatic_backfill_attempted_for = attempt_key
        if not tasks:
            return
        batch = tasks[:self._AUTO_BACKFILL_BATCH_SIZE]
        self._status.setText(f"자동 분봉 보완 준비 · {len(batch)}건" + (f" / 남은 {len(tasks)}건" if len(tasks) > len(batch) else ""))
        self._start_missing_backfill(tasks=batch, automatic=True)

    def _backfill_received(self, code: str, day: object, bars: object) -> None:
        if not isinstance(day, date) or not isinstance(bars, tuple):
            return
        bars = tuple(bar for bar in bars if bar.minute.date() == day)
        if not bars:
            self._repo.mark_bar_backfill(code, day, "실패", "해당 거래일의 분봉이 반환되지 않았습니다.")
            return
        self._repo.upsert_bars(code, bars, "after_close_confirmed", datetime.now())
        self._repo.mark_bar_backfill(code, day, "확정")
        if (
            self._selected_history_code == code and self._selected_history_range is not None
            and self._selected_history_range[0] <= day <= self._selected_history_range[1]
        ):
            self._history_chart.set_rows(self._load_selected_chart_rows())
            self._focus_selected_history()
            if self._active_episode is not None:
                self._show_trade_analysis(self._active_episode, self._load_selected_chart_rows())

    def _backfill_failed(self, code: str, day: object, message: str) -> None:
        if isinstance(day, date):
            self._repo.mark_bar_backfill(code, day, "실패", message)

    def _backfill_completed(self, success: int, failed: int) -> None:
        prefix = "자동 " if self._automatic_backfill_running else ""
        self._status.setText(f"{prefix}분봉 보완 완료 · 성공 {success}건 · 실패 {failed}건")
        self._render_summaries()
        self._refresh_backfill_table()

    def _backfill_finished(self) -> None:
        self._backfill_worker = None
        self._automatic_backfill_running = False

    def _stop_backfill(self) -> None:
        if self._backfill_worker is not None and self._backfill_worker.isRunning():
            self._backfill_worker.requestInterruption(); self._status.setText("현재 요청이 끝난 뒤 분봉 보완을 중지합니다.")

    def _refresh_backfill_table(self, *args: object) -> None:
        if not hasattr(self, "_backfill_table"):
            return
        states = self._repo.list_bar_backfill_states(date.today() + timedelta(days=1))
        selected = self._backfill_filter.currentText()
        if selected != "전체":
            states = tuple(value for value in states if value.state == selected)
        self._backfill_table.setRowCount(len(states))
        for row, state in enumerate(states):
            values = (state.trade_date.isoformat(), state.stock_code, state.state, f"{state.bar_count}개", state.message)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, (state.stock_code, state.trade_date))
                if column == 2:
                    colors = {"확정": "#2E7D32", "실패": "#D32F2F", "일부": "#EF6C00", "미조회": "#555555"}
                    item.setForeground(QBrush(QColor(colors.get(state.state, "#555555"))))
                self._backfill_table.setItem(row, column, item)
        self._backfill_table.resizeColumnsToContents()

    def _retry_selected_backfills(self) -> None:
        rows = sorted({index.row() for index in self._backfill_table.selectionModel().selectedRows()})
        tasks: list[tuple[str, date]] = []
        for row in rows:
            item = self._backfill_table.item(row, 0)
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], date):
                tasks.append(value)
        if not tasks:
            self._status.setText("재조회할 분봉 항목을 선택하세요."); return
        self._start_missing_backfill(tasks=tuple(tasks))

    def _refresh_statistics(self, *args: object) -> None:
        if not hasattr(self, "_stats_table"):
            return
        statistics = summarize_periods(self._episodes, self._stats_unit.currentText())
        total_profit = sum(value.realized_profit for value in statistics)
        trades = sum(value.trade_count for value in statistics)
        wins = sum(value.win_count for value in statistics)
        losses = sum(value.loss_count for value in statistics)
        closed = wins + losses
        tag_counts: dict[str, int] = {}
        rating_counts: dict[str, int] = {}
        for episode in self._episodes:
            review = self._repo.load_review(episode.group_id)
            rating_counts[review.rating] = rating_counts.get(review.rating, 0) + 1
            for tag in review.tags.replace("#", "").split(","):
                normalized = tag.strip()
                if normalized:
                    tag_counts[normalized] = tag_counts.get(normalized, 0) + 1
        common_tags = ", ".join(
            f"{name} {count}회" for name, count in sorted(tag_counts.items(), key=lambda value: (-value[1], value[0]))[:5]
        ) or "아직 복기 태그 없음"
        weak_count = rating_counts.get("아쉬움", 0)
        self._stats_summary.setText(
            f"현재 검색 범위 · 매매 {trades}건 · 수익 {wins}건 · 손실 {losses}건 · "
            f"승률 {(wins / closed * 100 if closed else 0):.1f}% · 실현손익 {total_profit:+,}원\n"
            f"반복 태그: {common_tags} · 아쉬움 평가 {weak_count}건"
        )
        self._stats_table.setRowCount(len(statistics))
        for row, value in enumerate(statistics):
            cells = (
                value.period, f"{value.trade_count}건", f"{value.win_count}건", f"{value.loss_count}건",
                f"{value.win_rate:.1f}%", f"{value.realized_profit:+,}원", f"{value.return_rate:+.2f}%",
            )
            for column, text_value in enumerate(cells):
                item = QTableWidgetItem(text_value)
                if column in (5, 6):
                    item.setForeground(QBrush(QColor("#D32F2F" if value.realized_profit > 0 else ("#1976D2" if value.realized_profit < 0 else "#555555"))))
                self._stats_table.setItem(row, column, item)
        self._stats_table.resizeColumnsToContents()

    def _show_selected_summary(self) -> None:
        row = self._summary_table.currentRow()
        item = self._summary_table.item(row, 0) if row >= 0 else None
        episode = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(episode, TradeEpisode):
            return
        if self._active_episode is not None and self._active_episode.group_id != episode.group_id:
            self._save_active_review()
        self._active_episode = episode
        self._load_active_review()
        summary = episode.summary
        related = episode.fills
        self._selected_history_code, self._selected_history_day = summary.stock_code, summary.trade_date
        self._selected_history_focus = related[0].filled_at if related else None
        self._selected_history_range = (
            min(fill.filled_at.date() for fill in related), max(fill.filled_at.date() for fill in related)
        ) if related else (summary.trade_date, summary.trade_date)
        self._history_chart.clear_daily_rows()
        self._history_daily_chart.clear_daily_rows()
        self._history_chart.set_fills(tuple(sorted(related, key=lambda value: value.filled_at)))
        self._history_daily_chart.set_fills(tuple(sorted(related, key=lambda value: value.filled_at)))
        chart_rows = self._load_selected_chart_rows()
        self._history_chart.set_rows(chart_rows)
        self._render_fills(related)
        if related and chart_rows:
            self._history_chart.focus_time(related[0].filled_at)
        self._show_trade_analysis(episode, chart_rows)
        self._sync_detached_charts()
        self._request_incomplete_trade_days(related)
        self._request_previous_chart_day(summary.stock_code, summary.trade_date)
        if self._history_interval.currentText() == "일봉" or self._dual_history_chart.isChecked():
            self._start_daily_chart(summary.stock_code, summary.trade_date, "history")

    def _request_previous_chart_day(self, code: str, day: date) -> None:
        """분봉 상단의 전일 종가와 연속 차트를 위해 직전 거래일을 한 번만 보완한다."""
        key = (code, day)
        if key in self._previous_chart_requested:
            return
        rows = self._repo.load_chart_bars(code, datetime.combine(day, time()))
        if any(datetime.fromisoformat(str(row[0])).date() < day for row in rows):
            self._previous_chart_requested.add(key)
            return
        if self._worker is not None and self._worker.isRunning():
            QTimer.singleShot(
                1_000,
                lambda target_code=code, target_day=day: self._request_previous_chart_day(target_code, target_day),
            )
            return
        self._previous_chart_requested.add(key)
        self._status.setText(f"{code} 직전 거래일 분봉 보완 중…")
        self._start_bar_confirmation(code, day, include_previous=True)

    @staticmethod
    def _sync_chart_scrollbar(scrollbar: QScrollBar, maximum: int, current: int, page: int) -> None:
        scrollbar.blockSignals(True)
        scrollbar.setRange(0, maximum); scrollbar.setPageStep(max(1, page)); scrollbar.setValue(current)
        scrollbar.setVisible(maximum > 0)
        scrollbar.blockSignals(False)

    @staticmethod
    def _sync_view_count_input(control: QSpinBox, count: int) -> None:
        control.blockSignals(True)
        control.setValue(max(0, int(count)))
        control.blockSignals(False)

    def _history_interval_changed(self, interval: str) -> None:
        self._history_chart.set_interval(interval)
        self._focus_selected_history()
        if interval == "일봉" and self._selected_history_code and self._selected_history_day is not None:
            self._start_daily_chart(self._selected_history_code, self._selected_history_day, "history")

    def _toggle_dual_history_chart(self, enabled: bool) -> None:
        self._show_dual_history_chart = bool(enabled)
        self._journal_settings.setValue("show_dual_history_chart", self._show_dual_history_chart)
        self._history_daily_title.setVisible(enabled); self._history_daily_chart.setVisible(enabled)
        if not enabled:
            return
        if self._history_interval.currentText() == "일봉":
            self._history_interval.setCurrentText("1분")
        if self._active_episode is not None:
            self._history_daily_chart.set_fills(self._active_episode.fills)
            self._history_daily_chart.set_setup_types(self._history_chart.setup_types())
        if self._selected_history_code and self._selected_history_day is not None:
            self._start_daily_chart(self._selected_history_code, self._selected_history_day, "history")

    def _open_detached_charts(self) -> None:
        if self._detached_chart_window is None:
            self._detached_chart_window = DetachedChartWindow(self._journal_settings, self)
            self._detached_chart_window.set_stock_choices(self._repo.load_monitor_stock_catalog(self._monitor_db))
            self._detached_chart_window.source_changed.connect(self._detached_source_changed)
        self._configure_detached_charts(); self._sync_detached_charts()
        self._detached_chart_window.show(); self._detached_chart_window.raise_(); self._detached_chart_window.activateWindow()
        if self._selected_history_code and self._selected_history_day is not None:
            self._start_daily_chart(self._selected_history_code, self._selected_history_day, "history")

    def _configure_detached_charts(self) -> None:
        if self._detached_chart_window is None: return
        self._detached_chart_window.configure(
            self._chart_background, self._ctrl_wheel_zoom_enabled, self._trade_value_threshold,
            self._daily_trade_value_threshold, self._show_trade_details, self._drawing_line_width,
            self._rectangle_color, self._line_color, self._horizontal_line_color, self._vertical_line_color,
        )
        for panel in self._detached_chart_window.panels:
            panel.chart.set_moving_average_styles(self._moving_average_styles)

    def _sync_detached_charts(self) -> None:
        if self._detached_chart_window is None: return
        episode = self._active_episode
        if episode is None:
            self._detached_chart_window.set_data(None, (), (), ()); return
        minute_rows = self._load_selected_chart_rows()
        daily_rows = self._repo.load_daily_bars(episode.summary.stock_code, episode.ended_at.date())
        custom_rows: dict[str, tuple[tuple[object, ...], ...]] = {}
        for code in self._detached_chart_window.selected_stock_codes():
            try:
                self._repo.import_live_bars(self._monitor_db, code, datetime.combine(episode.ended_at.date(), time()))
            except (OSError, sqlite3.Error):
                pass
            custom_rows[code] = self._repo.load_chart_bars_range(code, episode.started_at.date(), episode.ended_at.date())
            try: self._repo.import_monitor_daily_bars(self._monitor_db, code, episode.ended_at.date(), 250)
            except (OSError, sqlite3.Error): pass
            custom_rows[f"daily:{code}"] = self._repo.load_daily_bars(code, episode.ended_at.date())
        index_rows = {
            market: self._repo.load_monitor_market_index_bars(
                self._monitor_db, market, episode.started_at.date(), episode.ended_at.date(),
            )
            for market in ("kospi", "kosdaq")
        }
        for market in ("kospi", "kosdaq"):
            index_rows[f"daily:{market}"] = self._repo.load_monitor_market_index_daily(
                self._monitor_db, market, episode.ended_at.date(),
            )
        index_rows.update(custom_rows)
        self._detached_chart_window.set_data(
            episode, minute_rows, daily_rows, self._history_chart.setup_types(), index_rows,
        )
        if self._detached_chart_window.selected_index_markets():
            self._start_market_index_backfill(episode.ended_at.date())

    def _detached_source_changed(self) -> None:
        self._sync_detached_charts()
        if self._detached_chart_window is None or self._active_episode is None: return
        day = self._active_episode.ended_at.date()
        for code in self._detached_chart_window.selected_stock_codes():
            key = (code, day)
            if key in self._compare_stock_requested: continue
            self._start_daily_chart(code, day, f"detached:{code}")
            if self._worker is None or not self._worker.isRunning():
                self._compare_stock_requested.add(key)
                self._status.setText(f"비교 종목 분봉 보완 중 · {code}")
                self._start_bar_confirmation(code, day, include_previous=True)
            else:
                QTimer.singleShot(1_000, self._detached_source_changed)
            break

    def _start_market_index_backfill(self, day: date) -> None:
        if day in self._market_index_requested_days or (self._market_index_worker is not None and self._market_index_worker.isRunning()): return
        self._market_index_requested_days.add(day)
        worker = MarketIndexBackfillWorker(self._market_index_service, day)
        worker.completed.connect(self._market_index_backfill_completed)
        worker.failed.connect(lambda message: self._status.setText(f"시장지수 보완 실패 · {message}"))
        worker.finished.connect(worker.deleteLater); self._market_index_worker = worker; worker.start()

    def _market_index_backfill_completed(self, rows: object) -> None:
        if isinstance(rows, tuple) and len(rows) == 2 and isinstance(rows[0], dict) and isinstance(rows[1], dict):
            try:
                repository = MinuteBarRepository(self._monitor_db); repository.replace_market_index_minutes(rows[0])
                for market, daily_rows in rows[1].items(): repository.replace_market_index_daily(market, daily_rows)
            except Exception as error: self._status.setText(f"시장지수 저장 실패 · {error}"); return
            self._status.setText(f"코스피·코스닥 분봉·일봉 보완 · {len(rows[0])}개")
            self._sync_detached_charts()

    def _detached_chart_export_context(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        """전용 차트의 현재 구성 이미지에 붙일 복기 내용을 반환한다."""
        episode = self._active_episode
        if episode is None:
            return ("매매일지 차트", ())
        summary = episode.summary
        review = self._repo.load_review(episode.group_id)
        review_body = (
            f"평가: {review.rating} · 상태: {review.status}\n"
            f"매매 이유: {review.reason or '미작성'}\n"
            f"복기 메모: {review.review or '미작성'}\n"
            f"태그: {review.tags or '없음'}"
        )
        name = summary.stock_name or summary.stock_code
        title = f"{episode.started_at:%Y%m%d}_{name}_매매복기"
        return (
            title,
            (
                ("예상 매매유형", self._setup_label.toPlainText()),
                ("자동 분석", self._analysis_label.toPlainText()),
                ("직접 작성한 복기", review_body),
            ),
        )

    def _live_interval_changed(self, interval: str) -> None:
        self._chart.set_interval(interval)
        if interval == "일봉" and self._live_code:
            self._start_daily_chart(self._live_code, date.today(), "live")

    def _start_daily_chart(self, code: str, day: date, target: str) -> None:
        request = (code, day, target)
        try:
            self._repo.import_monitor_daily_bars(self._monitor_db, code, day, 250)
        except (OSError, sqlite3.Error):
            pass
        cached = self._repo.load_daily_bars(code, day)
        if cached:
            if target == "history" and code == self._selected_history_code:
                self._history_chart.set_daily_rows(cached)
                self._history_daily_chart.set_daily_rows(cached)
                self._focus_selected_history()
                self._sync_detached_charts()
            elif target == "live" and code == self._live_code:
                self._chart.set_daily_rows(cached)
            elif target.startswith("detached:"):
                self._sync_detached_charts()
            # 과거 복기용 일봉이 충분히 저장돼 있으면 같은 API를 반복 호출하지 않는다.
            if (target == "history" or target.startswith("detached:")) and day < date.today() and len(cached) >= 250:
                self._status.setText(f"저장된 일봉 {len(cached)}개 표시")
                return
        if self._daily_chart_worker is not None and self._daily_chart_worker.isRunning():
            self._pending_daily_chart = request
            return
        self._pending_daily_chart = None
        worker = DailyChartWorker(self._daily_chart_service, code, day, target)
        worker.completed.connect(self._daily_chart_received)
        worker.failed.connect(lambda message: self._status.setText(f"일봉 조회 실패 · {message}"))
        worker.finished.connect(self._daily_chart_finished); worker.finished.connect(worker.deleteLater)
        self._daily_chart_worker = worker; self._status.setText("최근 일봉 가져오는 중…"); worker.start()

    def _daily_chart_received(self, code: str, rows: object, target: str) -> None:
        if not isinstance(rows, tuple):
            return
        self._repo.upsert_daily_bars(code, rows)
        if target == "history" and code == self._selected_history_code:
            self._history_chart.set_daily_rows(rows)
            self._history_daily_chart.set_daily_rows(rows)
            self._focus_selected_history()
            self._sync_detached_charts()
            if self._active_episode is not None and self._active_episode.summary.stock_code == code:
                self._show_trade_analysis(self._active_episode, self._load_selected_chart_rows())
        elif target == "live" and code == self._live_code:
            self._chart.set_daily_rows(rows)
        elif target.startswith("detached:"):
            self._sync_detached_charts()
        self._status.setText(f"일봉 {len(rows)}개 확인")

    def _daily_chart_finished(self) -> None:
        self._daily_chart_worker = None
        pending, self._pending_daily_chart = self._pending_daily_chart, None
        if pending is not None:
            self._start_daily_chart(*pending)

    def _show_trade_analysis(self, episode: TradeEpisode, rows: tuple[tuple[object, ...], ...]) -> None:
        self._journal_news_button.setEnabled(True)
        linked_news = self._snapshot_news_repo.load_journal_linked(episode.group_id, episode.summary.stock_code) if self._snapshot_news_repo is not None else ()
        self._journal_news_button.setText(f"관련 뉴스 보기 · 고정 {len(linked_news)}건")
        try:
            self._repo.import_monitor_daily_bars(self._monitor_db, episode.summary.stock_code, episode.ended_at.date(), 250)
        except (OSError, sqlite3.Error):
            pass
        daily_rows = self._repo.load_daily_bars(episode.summary.stock_code, episode.ended_at.date())
        setup, pack_results = self._classify_with_strategy_packs(episode, rows, daily_rows)
        stored_setup = self._repo.load_trade_setup(episode.group_id)
        legacy_manual_type = stored_setup[1] if stored_setup is not None else ""
        self._repo.save_trade_setup(episode.group_id, setup, "")
        base_cycle_setups = self._active_strategy_pack.classify_cycles(episode, rows, daily_rows)
        cycle_episodes = split_trade_episode_cycles(episode)
        cycle_setups = tuple(
            self._select_custom_strategy_result(cycle, rows, daily_rows, base_cycle_setups[index])[0]
            for index, cycle in enumerate(cycle_episodes)
        )
        overrides = self._repo.load_trade_setup_cycle_overrides(episode.group_id)
        if legacy_manual_type and len(cycle_setups) == 1 and 0 not in overrides:
            normalized_legacy = self._normalize_setup_type(legacy_manual_type)
            self._repo.save_trade_setup_cycle_override(episode.group_id, 0, normalized_legacy)
            overrides[0] = normalized_legacy
        selected_types = tuple(self._normalize_setup_type(overrides.get(index, value.setup_type)) for index, value in enumerate(cycle_setups))
        selected_type = " · ".join(
            f"{index + 1}차 {value}" if len(selected_types) > 1 else value
            for index, value in enumerate(selected_types)
        )
        subtype_text = setup.subtype or "세부 유형 판정 보류"
        self._setup_label.setPlainText(
            f"예상 매매유형: {selected_type}\n세부 유형: {subtype_text}\n"
            + " / ".join(setup.evidence)
            + ("\n기본·전략팩 결과: " + " · ".join(pack_results) if pack_results else "")
        )
        self._history_chart.set_setup_types(selected_types)
        self._history_daily_chart.set_setup_types(selected_types)
        self._sync_detached_charts()
        self._loading_setup = True
        self._setup_cycles.setRowCount(len(cycle_setups))
        for index, cycle_setup in enumerate(cycle_setups):
            self._setup_cycles.setItem(index, 0, QTableWidgetItem(f"{index + 1}차"))
            self._setup_cycles.setItem(index, 1, QTableWidgetItem(cycle_setup.setup_type))
            self._setup_cycles.setItem(index, 2, QTableWidgetItem(cycle_setup.subtype or "판정 보류"))
            self._setup_cycles.setItem(index, 3, QTableWidgetItem(f"{cycle_setup.confidence}%"))
            editor = QComboBox()
            editor.addItem("자동 판정 사용")
            available_types = tuple(dict.fromkeys((*TRADE_SETUP_TYPES, *(item for pack in self._strategy_packs if pack.enabled for item in pack.setup_types))))
            editor.addItems(available_types)
            editor.setCurrentText(self._normalize_setup_type(overrides[index]) if index in overrides else "자동 판정 사용")
            editor.currentTextChanged.connect(
                lambda value, cycle_index=index: self._setup_cycle_override_changed(cycle_index, value)
            )
            self._setup_cycles.setCellWidget(index, 4, editor)
        self._setup_cycles.resizeColumnsToContents()
        row_height = self._setup_cycles.verticalHeader().defaultSectionSize()
        header_height = self._setup_cycles.horizontalHeader().height()
        self._setup_cycles.setFixedHeight(min(260, header_height + row_height * max(1, len(cycle_setups)) + 6))
        self._loading_setup = False
        row = self._summary_table.currentRow()
        if row >= 0:
            self._summary_table.setItem(row, 14, QTableWidgetItem(selected_type))
        try:
            entry_snapshots = self._repo.load_entry_snapshots(
                episode.summary.stock_code,
                episode.started_at - timedelta(minutes=1),
                episode.ended_at + timedelta(minutes=1),
            )
        except sqlite3.Error:
            entry_snapshots = ()
        if self._snapshot_news_repo is not None:
            enriched_snapshots = []
            for snapshot in entry_snapshots:
                if snapshot.news:
                    enriched_snapshots.append(snapshot); continue
                news = news_at_execution(self._snapshot_news_repo, snapshot)
                if news:
                    self._repo.save_snapshot_news_backfill(snapshot.execution_key, news)
                    snapshot = replace(snapshot, news=news)
                enriched_snapshots.append(snapshot)
            entry_snapshots = tuple(enriched_snapshots)
        def cycle_snapshot_values(cycle: TradeEpisode):
            return tuple(
                snapshot for snapshot in entry_snapshots
                if cycle.started_at - timedelta(minutes=1) <= snapshot.executed_at <= cycle.ended_at + timedelta(minutes=1)
            )

        def remaining_unverifiable(cycle: TradeEpisode, original: tuple[str, ...]) -> tuple[str, ...]:
            """이미 저장된 진입 스냅샷과 진짜 미확인 항목을 구분한다."""
            snapshots = cycle_snapshot_values(cycle)
            if not snapshots:
                return original
            entry = next((value for value in snapshots if value.side == "매수"), snapshots[0])
            result: list[str] = []
            for item in original:
                if "시장 주도주 여부" in item and entry.rank is not None:
                    result.append("1강 기준의 지속적인 시장 관심 여부(진입 순간 순위는 확인됨)")
                    continue
                if item.startswith("당시 뉴스") or item.startswith("급락 원인"):
                    missing = []
                    if not entry.news: missing.append("뉴스·재료")
                    if not entry.themes: missing.append("테마")
                    if not entry.orderbook: missing.append("호가·매수세")
                    if not entry.investor_flow: missing.append("외국인·기관 수급")
                    if not entry.market_state: missing.append("시장 상태")
                    if missing:
                        result.append("당시 " + "·".join(missing))
                    continue
                result.append(item)
            return tuple(dict.fromkeys(result))

        analyses = tuple(
            analyze_trade_episode(
                cycle, rows, personal_rules=self._load_personal_rules(),
                trade_value_threshold_eok=self._trade_value_threshold,
                setup_type=selected_types[index],
                setup_confidence=90 if index in overrides else cycle_setups[index].confidence,
                setup_evidence=(
                    (f"사용자가 {selected_types[index]} 유형으로 직접 확정했습니다.",)
                    if index in overrides else cycle_setups[index].evidence
                ),
                lesson_warnings=cycle_setups[index].warnings if index not in overrides else (),
                unverifiable_items=remaining_unverifiable(
                    cycle,
                    self._active_strategy_pack.unverifiable_for(selected_types[index])
                    if index in overrides else cycle_setups[index].unverifiable,
                ),
            )
            for index, cycle in enumerate(cycle_episodes)
        )
        def rate(value: float | None) -> str:
            return "계산 대기" if value is None else f"{value:+.2f}%"
        data_state = "분봉 확인" if all(value.complete for value in analyses) else "분봉 범위 일부만 반영"
        personal_rules = self._load_personal_rules()
        lesson_counts: dict[int, int] = {}
        for rule in self._structured_personal_rules:
            lesson_counts[rule.lesson] = lesson_counts.get(rule.lesson, 0) + 1
        rules_state = (
            "개인원칙 강의구조 연결 · " + " · ".join(f"{lesson}강 {count}문단" for lesson, count in sorted(lesson_counts.items()))
            if lesson_counts else f"개인원칙 문서 {len(personal_rules)}문단 연결(강의구조 없음)" if personal_rules else "개인원칙 문서 미연결"
        )
        snapshot_state = (
            f"진입 스냅샷 {len(entry_snapshots)}건 연결"
            if entry_snapshots else "당시 뉴스·테마·매수세·시장 수급 스냅샷 없음"
        )
        active_pack_names = [self._active_strategy_pack.manifest.name] + [
            pack.name for pack in self._strategy_packs
            if pack.pack_id != "mimosa_v1" and pack.enabled and pack.review_status == "approved"
        ]
        self._analysis_data_status.setText(
            f"활성 전략팩 {', '.join(active_pack_names)}  ·  {rules_state}  ·  {data_state}  ·  {snapshot_state}"
        )
        average_score = round(sum(value.discipline_score for value in analyses) / len(analyses))
        evaluated_count = sum(value.evaluated_rule_count for value in analyses)
        overall_verdict = (
            "자동 확인 가능한 설정 기준 없음" if not evaluated_count
            else "자동 확인 가능한 설정 기준 충족" if average_score >= 85
            else "자동 확인 가능한 설정 기준 일부 주의" if average_score >= 60
            else "자동 확인 가능한 설정 기준 주의"
        )
        blocks = [
            f"하루 종합 · {overall_verdict} · 자동 확인 기준 {evaluated_count}개 · 평균 {average_score}점 · "
            f"총 {len(analyses)}회차 · 전체 실현 {episode.summary.return_rate:+.2f}%"
        ]
        if linked_news:
            blocks.append("\n[매매일지 대표 뉴스]")
            blocks.extend(
                f"{item.published_at.astimezone().strftime('%H:%M') if item.published_at else '--:--'} · "
                f"{item.assessment.outlook or '미분류'} · {item.title}"
                for item in linked_news
            )
        lesson_by_setup = {
            "주도주 돌파": 2, "테마주 돌파": 3, "신규주": 4,
            "종가베팅": 5, "과대낙폭": 6, "낙주": 6,
        }
        for index, analysis in enumerate(analyses):
            labels = " · ".join(analysis.labels)
            selected_pack = self._pack_for_result(cycle_setups[index])
            if selected_pack is None:
                applicable_lessons = {1, lesson_by_setup.get(selected_types[index], -1)}
                relevant_rule_count = sum(1 for rule in self._structured_personal_rules if rule.lesson in applicable_lessons)
                pack_name = self._active_strategy_pack.manifest.name
                source_text = self._active_strategy_pack.source_text(selected_types[index])
            else:
                draft = self._repo.load_strategy_pack_draft(selected_pack.pack_id)
                relevant_rule_count = len(draft.rules) if draft else 0
                pack_name = selected_pack.name; source_text = selected_pack.source_description
            blocks.append(
                f"\n[{index + 1}차 · {selected_types[index]}] 전략팩: {pack_name}\n"
                f"적용 강의: {source_text}\n"
                f"구조화 강의항목 {relevant_rule_count}개 참고자료 연결 · {analysis.verdict} · 실제 자동 확인 {analysis.evaluated_rule_count}개 · "
                f"점수 {analysis.discipline_score}점 · "
                f"보유 {analysis.holding_text} · 실현 {analysis.realized_return_rate:+.2f}%\n"
                f"분석 신뢰도 {analysis.confidence}% · MFE(보유 중 최대 유리폭) {rate(analysis.max_favorable_rate)} · "
                f"MAE(보유 중 최대 불리폭) {rate(analysis.max_adverse_rate)} · {labels}\n"
                f"확인된 진입 근거: {' / '.join(analysis.setup_evidence) or '가격 데이터로 확인된 근거 없음'}\n"
                f"강의 기준 주의: {' / '.join(analysis.lesson_warnings) or '가격 데이터에서 확인된 주의 없음'}\n"
                f"현재 판정 불가: {' / '.join(analysis.unverifiable_items) or '추가 확인 항목 없음'}\n"
                f"개인 원칙 충족: {' / '.join(analysis.positive_points) or '확인된 충족 항목 없음'}\n"
                f"개인 원칙 주의: {' / '.join(analysis.negative_points) or '확인된 위반 없음'}\n"
                f"사용자 설정 기준 충족: {' / '.join(analysis.user_setting_matches) or '확인된 충족 항목 없음'}\n"
                f"사용자 설정 기준 주의: {' / '.join(analysis.user_setting_warnings) or '설정 기준 경고 없음'}\n"
                f"프로그램 보조정보: {' / '.join(analysis.program_notes) or '추가 참고사항 없음'}\n"
                f"프로그램 기본 경고: {' / '.join(analysis.program_warnings) or '기본 경고 없음'}\n"
                f"다음 행동: {' / '.join(analysis.next_actions[:5]) or '현재 기준을 유지하며 표본을 더 확인하세요.'}"
            )
            cycle = cycle_episodes[index]
            cycle_snapshots = cycle_snapshot_values(cycle)
            entry = next((snapshot for snapshot in cycle_snapshots if snapshot.side == "매수"), cycle_snapshots[0] if cycle_snapshots else None)
            if entry is not None:
                pressure = entry.orderbook or {}
                investor = entry.investor_flow or {}
                program = investor.get("program_trade", {}) if isinstance(investor.get("program_trade"), dict) else {}
                investor_pending = investor.get("status") == "pending_close" or (
                    investor.get("foreign_net_buy_quantity") == 0
                    and investor.get("institution_net_buy_quantity") == 0
                    and str(investor.get("as_of_date", "")) == entry.executed_at.strftime("%Y%m%d")
                    and entry.executed_at.time() < time(20, 5)
                )
                foreign_text = "집계 전" if investor_pending else investor.get("foreign_net_buy_quantity", "-")
                institution_text = "집계 전" if investor_pending else investor.get("institution_net_buy_quantity", "-")
                theme_text = ", ".join(
                    f"{theme}({entry.theme_ranks.get(theme)}위)" if entry.theme_ranks and theme in entry.theme_ranks else theme
                    for theme in entry.themes
                ) or "없음"
                blocks.append(
                    "진입 스냅샷: "
                    f"실시간 {entry.rank if entry.rank is not None else '-'}위 · "
                    f"1분 {entry.trade_value_1m_eok or 0:.2f}억 · 5분 {entry.trade_value_5m_eok or 0:.2f}억 · "
                    f"신고가 거리 {entry.high_distance_percent:.2f}% · " if entry.high_distance_percent is not None else
                    "진입 스냅샷: 신고가 거리 자료 없음 · "
                )
                execution_strength = pressure.get("execution_strength")
                buy_share = pressure.get("buy_share_percent")
                strength_text = f"{float(execution_strength):.2f}" if isinstance(execution_strength, (int, float)) else "-"
                buy_share_text = f"{float(buy_share):.2f}%" if isinstance(buy_share, (int, float)) else "-"
                blocks.append(
                    f"테마: {theme_text} · 체결강도 {strength_text} · "
                    f"최근 60초 매수비중 {buy_share_text} · "
                    f"외국인 순매수 {foreign_text} · "
                    f"기관 순매수 {institution_text} · "
                    f"수급 기준 {investor.get('market_basis', '-')} · "
                    f"프로그램 순매수 {program.get('net_buy_amount_million_won', '-')}백만원/"
                    f"{program.get('net_buy_quantity', '-')}주 · "
                    f"직전 프로그램 갱신 변화 {program.get('net_buy_amount_change_million_won', '-')}백만원/"
                    f"{program.get('net_buy_quantity_change', '-')}주 · 관련 뉴스 {len(entry.news)}건"
                )
                kospi = entry.market_state.get("kospi", {}) if entry.market_state else {}
                kosdaq = entry.market_state.get("kosdaq", {}) if entry.market_state else {}
                if kospi or kosdaq:
                    blocks.append(
                        f"시장: 코스피 {kospi.get('index', '-')} ({kospi.get('change_rate', '-')}%) · "
                        f"거래대금 {kospi.get('trade_value_eok', '-')}억 / "
                        f"코스닥 {kosdaq.get('index', '-')} ({kosdaq.get('change_rate', '-')}%) · "
                        f"거래대금 {kosdaq.get('trade_value_eok', '-')}억 · "
                        f"시장 조회 {entry.market_state.get('observed_at', '-')}"
                    )
        self._analysis_label.setPlainText("\n".join(blocks))

    def _open_active_news(self) -> None:
        episode = self._active_episode
        if episode is None:
            return
        request_path = self._monitor_db.parent / "journal_news_request.json"
        document = {
            "request_id": monotonic_time.time_ns(),
            "code": episode.summary.stock_code,
            "name": episode.summary.stock_name,
            "group_id": episode.group_id,
            "trade_date": episode.summary.trade_date.isoformat(),
        }
        temporary = request_path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            temporary.replace(request_path)
            self._review_saved.setText("뉴스창에서 대표 기사를 선택해 매매일지에 추가할 수 있습니다.")
        except OSError as error:
            self._review_saved.setText(f"뉴스창 요청 실패: {error}")

    def _classify_with_strategy_packs(
        self, episode: TradeEpisode, rows: tuple[tuple[object, ...], ...], daily_rows: tuple[tuple[object, ...], ...],
    ) -> tuple[object, tuple[str, ...]]:
        base = self._active_strategy_pack.classify(episode, rows, daily_rows)
        return self._select_custom_strategy_result(episode, rows, daily_rows, base)

    def _select_custom_strategy_result(
        self, episode: TradeEpisode, rows: tuple[tuple[object, ...], ...],
        daily_rows: tuple[tuple[object, ...], ...], base: object,
    ) -> tuple[object, tuple[str, ...]]:
        candidates = [("기본분석", base)]
        for pack in self._strategy_packs:
            if pack.pack_id == "mimosa_v1" or not pack.enabled or pack.review_status != "approved": continue
            draft = self._repo.load_strategy_pack_draft(pack.pack_id)
            if draft is None: continue
            result = evaluate_strategy_pack(pack, draft, episode, rows, daily_rows)
            if result is not None: candidates.append((pack.name, result))
        typed_candidates = tuple((name, result) for name, result in candidates)
        selected_name, selected = select_strategy_candidate(typed_candidates, self._strategy_result_mode)
        mode_label = STRATEGY_RESULT_MODES.get(self._strategy_result_mode, STRATEGY_RESULT_MODES["together"])
        comparison = tuple(
            f"{name} {getattr(result, 'setup_type', '기타')} {getattr(result, 'confidence', 0)}%"
            + ("(최종)" if name == selected_name else "")
            for name, result in candidates
        )
        return selected, (f"표시방식 {mode_label}", *comparison)

    def _normalize_setup_type(self, value: str) -> str:
        custom = {item for pack in self._strategy_packs if pack.enabled and pack.review_status == "approved" for item in pack.setup_types}
        return value if value in custom else normalize_trade_setup_type(value)

    def _pack_for_result(self, result: object) -> StrategyPackManifest | None:
        subtype = str(getattr(result, "subtype", ""))
        return next((
            pack for pack in self._strategy_packs
            if pack.pack_id != "mimosa_v1" and pack.enabled and subtype.startswith(pack.name)
        ), None)

    def _load_personal_rules(self) -> tuple[str, ...]:
        return self._personal_rules

    def _setup_cycle_override_changed(self, cycle_index: int, value: str) -> None:
        if self._loading_setup or self._active_episode is None:
            return
        manual_type = "" if value == "자동 판정 사용" else value
        self._repo.save_trade_setup_cycle_override(self._active_episode.group_id, cycle_index, manual_type)
        rows = self._load_selected_chart_rows()
        self._show_trade_analysis(self._active_episode, rows)

    def _load_active_review(self) -> None:
        if self._active_episode is None:
            return
        review = self._repo.load_review(self._active_episode.group_id)
        self._loading_review = True
        self._review_reason.setPlainText(review.reason); self._review_note.setPlainText(review.review)
        self._review_tags.setText(review.tags); self._review_rating.setCurrentText(review.rating or "보통")
        self._review_status.setCurrentText(review.status or "미작성")
        self._loading_review = False
        self._review_saved.setText("저장된 복기" if any((review.reason, review.review, review.tags)) else "아직 작성하지 않음")

    def _review_changed(self, *args: object) -> None:
        if self._loading_review or self._active_episode is None:
            return
        self._review_saved.setText("저장 대기…")
        self._review_save_timer.start(700)

    def _save_active_review(self) -> None:
        if self._active_episode is None or self._loading_review:
            return
        self._review_save_timer.stop()
        review = TradeReview(
            self._active_episode.group_id, self._review_reason.toPlainText().strip(),
            self._review_note.toPlainText().strip(), self._review_tags.text().strip(),
            self._review_rating.currentText(), self._review_status.currentText(),
        )
        self._repo.save_review(review)
        self._review_saved.setText("저장됨")
        row = self._summary_table.currentRow()
        if row >= 0:
            self._summary_table.setItem(row, 13, QTableWidgetItem(review.status))

    def _selected_episodes(self) -> tuple[TradeEpisode, ...]:
        rows = sorted({index.row() for index in self._summary_table.selectionModel().selectedRows()})
        values: list[TradeEpisode] = []
        for row in rows:
            item = self._summary_table.item(row, 0)
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(value, TradeEpisode):
                values.append(value)
        return tuple(values)

    def _merge_selected_groups(self) -> None:
        episodes = self._selected_episodes()
        if len(episodes) < 2:
            self._status.setText("합칠 매매 묶음을 두 개 이상 선택하세요."); return
        if len({episode.summary.stock_code for episode in episodes}) != 1:
            self._status.setText("서로 같은 종목의 매매 묶음만 합칠 수 있습니다."); return
        group_id = "manual:" + uuid.uuid4().hex
        keys = tuple(trade_fill_key(fill) for episode in episodes for fill in episode.fills)
        self._repo.assign_group(keys, group_id); self._status.setText(f"매매 묶음 {len(episodes)}개를 합쳤습니다.")
        self.reload_history()

    def _split_selected_fills(self) -> None:
        rows = sorted({index.row() for index in self._fills_table.selectionModel().selectedRows()})
        fills: list[TradeFill] = []
        for row in rows:
            item = self._fills_table.item(row, 0)
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(value, TradeFill):
                fills.append(value)
        if not fills:
            self._status.setText("새 묶음으로 분리할 체결행을 선택하세요."); return
        if len({fill.stock_code for fill in fills}) != 1:
            self._status.setText("같은 종목 체결만 하나의 묶음으로 만들 수 있습니다."); return
        self._repo.assign_group(tuple(trade_fill_key(fill) for fill in fills), "manual:" + uuid.uuid4().hex)
        self._status.setText(f"선택 체결 {len(fills)}건을 새 묶음으로 분리했습니다."); self.reload_history()

    def _reset_selected_groups(self) -> None:
        episodes = self._selected_episodes()
        if not episodes:
            self._status.setText("자동분류로 되돌릴 묶음을 선택하세요."); return
        keys = tuple(trade_fill_key(fill) for episode in episodes for fill in episode.fills)
        self._repo.clear_group_assignments(keys); self._status.setText("선택 묶음을 자동분류로 되돌렸습니다.")
        self.reload_history()

    def _show_selected_fill(self) -> None:
        row = self._fills_table.currentRow()
        self._show_fill_at_row(row)

    def _show_fill_at_row(self, row: int) -> None:
        item = self._fills_table.item(row, 0) if row >= 0 else None
        fill = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(fill, TradeFill):
            return
        self._selected_history_code, self._selected_history_day = fill.stock_code, fill.filled_at.date()
        self._selected_history_focus = fill.filled_at
        related = (
            self._active_episode.fills
            if self._active_episode is not None and fill in self._active_episode.fills else
            tuple(value for value in self._visible_fills if value.stock_code == fill.stock_code and value.filled_at.date() == fill.filled_at.date())
        )
        self._selected_history_range = (
            min(value.filled_at.date() for value in related), max(value.filled_at.date() for value in related)
        ) if related else (fill.filled_at.date(), fill.filled_at.date())
        self._history_chart.set_fills(related)
        rows = self._repo.load_bars(fill.stock_code, fill.filled_at)
        chart_rows = self._load_selected_chart_rows()
        self._history_chart.set_rows(chart_rows)
        self._focus_selected_history()
        self._request_incomplete_trade_days(related)

    def _focus_selected_history(self) -> None:
        if self._selected_history_focus is not None:
            self._history_chart.focus_time(self._selected_history_focus)

    def _load_selected_chart_rows(self) -> tuple[tuple[object, ...], ...]:
        if not self._selected_history_code or self._selected_history_range is None:
            return ()
        return self._repo.load_chart_bars_range(
            self._selected_history_code, self._selected_history_range[0], self._selected_history_range[1],
        )

    def _request_incomplete_trade_days(self, fills: tuple[TradeFill, ...]) -> None:
        tasks: list[tuple[str, date]] = []
        for day in sorted({fill.filled_at.date() for fill in fills}):
            day_fills = tuple(fill for fill in fills if fill.filled_at.date() == day)
            rows = self._repo.load_bars(self._selected_history_code, datetime.combine(day, time()))
            if not rows:
                tasks.append((self._selected_history_code, day)); continue
            first_bar = datetime.fromisoformat(str(rows[0][0]))
            last_bar = datetime.fromisoformat(str(rows[-1][0])) + timedelta(minutes=1)
            if first_bar > min(fill.filled_at for fill in day_fills) or last_bar <= max(fill.filled_at for fill in day_fills):
                tasks.append((self._selected_history_code, day))
        if tasks and (self._backfill_worker is None or not self._backfill_worker.isRunning()):
            self._status.setText(f"체결 위치 누락 분봉 보완 중 · {len(tasks)}일")
            self._start_missing_backfill(tasks=tuple(tasks), automatic=True)

    def refresh(self) -> None:
        if not self._live_code:
            return
        now = datetime.now()
        try:
            self._repo.import_live_bars(self._monitor_db, self._live_code, now)
            rows = self._repo.load_bars(self._live_code, now)
            chart_rows = self._repo.load_chart_bars(self._live_code, now)
        except Exception:
            # 메인 DB가 쓰는 순간과 겹치면 다음 1초 갱신에서 다시 시도한다.
            return
        self._table.setRowCount(len(rows))
        self._chart.set_rows(chart_rows)
        current = now.replace(second=0, microsecond=0)
        for row_index, row in enumerate(rows):
            minute = datetime.fromisoformat(str(row[0]))
            values = (minute.strftime("%H:%M"), *row[1:6], f"{float(row[6]):,.2f}")
            source = str(row[7])
            state = "장 종료 확정" if source == "after_close_confirmed" else ("확인" if source == "api_confirmed" else ("진행 중·임시" if minute >= current else "보완 대기"))
            for column, value in enumerate((*values, state)):
                self._table.setItem(row_index, column, QTableWidgetItem(str(value)))
        if rows:
            self._table.scrollToBottom()

    def _confirm_on_new_minute(self) -> None:
        if not self._live_code:
            return
        now = datetime.now()
        key = now.strftime("%Y%m%d%H%M")
        if now.second >= 3 and key != self._last_confirmed_minute:
            self._last_confirmed_minute = key
            self.confirm_completed_bars()

    def confirm_completed_bars(self) -> None:
        if not self._live_code or (self._worker is not None and self._worker.isRunning()):
            return
        self._status.setText("완료 분봉 확인 중…")
        self._start_bar_confirmation(self._live_code)

    def _start_bar_confirmation(self, code: str, target_day: date | None = None, include_previous: bool = False) -> None:
        worker = ConfirmWorker(self._minute_service, code, target_day, include_previous)
        worker.completed.connect(self._confirmed)
        worker.failed.connect(lambda message: self._status.setText(f"확인 실패 · {message}"))
        worker.finished.connect(self._worker_finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker; worker.start()

    def _worker_finished(self) -> None:
        self._worker = None

    def _confirmed(self, code: str, bars: object, checked_at: object, source: str) -> None:
        if isinstance(bars, tuple) and isinstance(checked_at, datetime):
            self._repo.upsert_bars(code, bars, source, checked_at)
            self._status.setText(f"{checked_at:%H:%M:%S} 확인 · {len(bars)}개")
            if self._selected_history_day is not None and code == self._selected_history_code:
                rows = self._repo.load_bars(code, datetime.combine(self._selected_history_day, time()))
                self._history_chart.set_rows(self._load_selected_chart_rows())
                self._focus_selected_history()
                if self._active_episode is not None and self._active_episode.summary.stock_code == code:
                    self._show_trade_analysis(self._active_episode, self._load_selected_chart_rows())
            if self._live_code == code:
                self.refresh()
            if self._detached_chart_window is not None:
                active_code = (
                    self._active_episode.summary.stock_code
                    if self._active_episode is not None
                    else ""
                )
                if code == active_code or code in self._detached_chart_window.selected_stock_codes():
                    self._sync_detached_charts()

    def shutdown(self) -> None:
        self._refresh_timer.stop(); self._confirm_timer.stop(); self._auto_backfill_timer.stop(); self._history_sync_timer.stop()
        for attribute in ("_worker", "_history_worker", "_backfill_worker", "_daily_chart_worker", "_market_index_worker"):
            worker = getattr(self, attribute, None)
            try:
                if worker is not None and worker.isRunning():
                    worker.requestInterruption(); worker.wait(3_000)
            except RuntimeError:
                # deleteLater로 이미 정리된 QThread 래퍼는 다시 조회하지 않는다.
                pass
            setattr(self, attribute, None)

    def closeEvent(self, event: QCloseEvent) -> None:
        settings = QSettings("KiwoomMonitor", "TradingJournalWindow")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("history_splitter", self._history_splitter.saveState())
        settings.setValue("chart_interval", self._history_interval.currentText())
        settings.sync()
        # 매매일지는 실시간 구독을 유지하지 않는다. 보조 창을 먼저 닫고
        # 본창의 정상 close를 승인해 Qt가 마지막 창 종료와 함께 이벤트
        # 루프까지 끝내도록 한다. app.quit()만 호출하면 Windows에서 위젯은
        # 삭제됐지만 이벤트 루프가 남아 다음 show 명령이 죽은 위젯으로
        # 전달되는 반쪽 종료가 생길 수 있다.
        detached = self._detached_chart_window
        if detached is not None:
            try:
                detached.close()
            except RuntimeError:
                pass
        self.shutdown()
        event.accept()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.setProperty("journal_terminating", True)
            app.exit(0)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--monitor-database", required=True)
    parser.add_argument("--journal-database", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--command-file", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    options = parser.parse_args(arguments)
    crash_log = Path(options.journal_database).with_name("journal-crash.log")
    def record_uncaught(error_type: type[BaseException], error: BaseException, trace: object) -> None:
        try:
            with crash_log.open("a", encoding="utf-8") as output:
                output.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] uncaught exception\n")
                traceback.print_exception(error_type, error, trace, file=output)
        finally:
            sys.__excepthook__(error_type, error, trace)
    sys.excepthook = record_uncaught
    _set_taskbar_app_id()
    # 프로세스가 먼저 뜬 뒤 첫 show 명령을 100ms 폴링으로 받으므로, 표시된
    # 창이 잠시 없다는 이유만으로 Qt가 시작 직후 종료되면 안 된다. 실제
    # 본창 closeEvent에서는 journal_terminating 표시 후 app.exit(0)으로
    # 프로세스를 명시적으로 끝낸다.
    app = QApplication(sys.argv[:1]); app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(_application_icon_path())))
    window = JournalWindow(Path(options.monitor_database), Path(options.journal_database), Path(options.config))
    window.setWindowIcon(app.windowIcon())
    command_path = Path(options.command_file); last_id = -1
    state_path = command_path.with_name("journal_process.pid")
    state_path.write_text(str(os.getpid()), encoding="ascii")
    parent_pid = options.parent_pid
    parent_missing_since: float | None = None

    def poll() -> None:
        nonlocal last_id, parent_pid, parent_missing_since
        if bool(app.property("journal_terminating")):
            return
        try:
            command = json.loads(command_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            command = {}
        try:
            requested_parent = int(command.get("parent_pid", 0))
        except (TypeError, ValueError):
            requested_parent = 0
        if requested_parent > 0 and _parent_is_alive(requested_parent):
            parent_pid = requested_parent
            parent_missing_since = None
        if not _parent_is_alive(parent_pid):
            now = monotonic_time.monotonic()
            if parent_missing_since is None:
                parent_missing_since = now
            elif now - parent_missing_since >= 5.0:
                app.setProperty("journal_terminating", True)
                timer.stop(); window.shutdown(); app.exit(0)
            return
        parent_missing_since = None
        request_id = int(command.get("request_id", -1))
        if request_id <= last_id:
            return
        last_id = request_id
        if command.get("action") == "shutdown":
            app.setProperty("journal_terminating", True)
            timer.stop(); window.shutdown(); app.exit(0); return
        if command.get("action") == "parent":
            return
        if command.get("action") == "sync":
            window._quit_after_history_sync = True
            if not window._auto_sync_history_once():
                window._quit_after_history_sync = False
                app.setProperty("journal_terminating", True)
                timer.stop(); window.shutdown(); app.exit(0)
            return
        window.set_stock(str(command.get("code", "")), str(command.get("name", "")))
        window.showNormal(); window.raise_(); window.activateWindow()

    timer = QTimer(); timer.timeout.connect(poll); timer.start(100)
    try:
        return app.exec()
    finally:
        try:
            if state_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                state_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
