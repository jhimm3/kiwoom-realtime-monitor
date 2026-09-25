from __future__ import annotations

import os
import tempfile
import threading
import unittest
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QRect, QSettings, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from kiwoom_monitor.presentation.stock_news_window import StockNewsWindow
from kiwoom_monitor.presentation.news_view_model import StoredNewsEvidence
from kiwoom_monitor.presentation.news_workers import NewsEvidenceWorker
from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import NewsAISettings, StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis
from kiwoom_monitor.application.news_grouping import NewsEventGroup
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import (
    StoredAINewsAnalysis, news_identity,
)
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.journal_process import JournalWindow
from kiwoom_monitor.news_process import _apply_show_command
from kiwoom_monitor.presentation.main_window import MainWindow
from kiwoom_monitor.presentation.news_window_coordinator import NewsWindowCoordinator


class StockNewsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_ai_failure_is_reported_without_modal_message_box(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            window._window_settings = QSettings(str(root / "window.ini"), QSettings.Format.IniFormat)

            with patch.object(QMessageBox, "warning") as warning:
                window._on_ai_failed("일시적인 API 오류")

            warning.assert_not_called()
            self.assertIn("일시적인 API 오류", window._status_label.text())
            window.shutdown()

    def test_window_mode_can_switch_between_independent_and_attached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = QWidget()
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3", main)
            window._window_settings = QSettings(str(root / "window.ini"), QSettings.Format.IniFormat)

            window._apply_window_mode("attached", persist=False)
            self.assertIs(window.parentWidget(), main)
            window._apply_window_mode("independent", persist=False)
            self.assertIsNone(window.parentWidget())

            window.shutdown()
            main.close()

    def test_separate_process_window_offers_three_display_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            window._window_settings = QSettings(str(root / "window.ini"), QSettings.Format.IniFormat)

            self.assertEqual(
                ["independent", "linked", "docked_right", "docked_left", "docked_top", "docked_bottom"],
                [window._window_mode.itemData(index) for index in range(window._window_mode.count())],
            )
            window._apply_window_mode("docked_left")
            self.assertEqual("docked_left", window._window_settings.value("window_mode"))

            window.shutdown()

    def test_judgment_double_click_starts_ai_and_title_double_click_opens_article(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            window._window_settings = QSettings(str(root / "window.ini"), QSettings.Format.IniFormat)

            with patch.object(window, "_item_index", side_effect=lambda row: row):
                with patch.object(window, "_select_news_cell") as select, \
                        patch.object(window, "_analyze_selected") as analyze, \
                        patch.object(window, "_open_item") as open_item:
                    window._on_news_cell_double_clicked(2, 3)
                    select.assert_called_once_with(2, 3)
                    analyze.assert_called_once_with(automatic=False)
                    open_item.assert_not_called()

                    select.reset_mock(); analyze.reset_mock(); open_item.reset_mock()
                    window._on_news_cell_double_clicked(1, 4)
                    select.assert_called_once_with(1, 4)
                    analyze.assert_not_called()
                    open_item.assert_called_once_with(1)

            window.shutdown()

    def test_ai_detail_shows_scores_and_evidence_before_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            item = StockNewsItem(
                "테스트기업 공급계약", "500억원 계약", "https://example.com", "https://example.com",
                datetime.now(UTC), assess_stock_news("테스트기업", "테스트기업 공급계약", "500억원 계약"),
            )
            stored = SimpleNamespace(
                analysis=AINewsAnalysis("원문 요약", "긍정", 87, "판단 이유", ("긍정 근거",), ("부정 근거",), "수주·계약"),
                provider="gemini", model="test-model",
            )

            window._ai_result_cache[news_identity(item)] = stored
            with patch.object(window._ai_repository, "load") as load:
                detail = window._ai_html(item)
            load.assert_not_called()

            self.assertIn(f"관련성 {item.assessment.relevance_score}점", detail)
            self.assertIn("신뢰도 87%", detail)
            self.assertLess(detail.index("<b>이유:</b>"), detail.index("<b>원문 요약:</b>"))
            self.assertLess(detail.index("<b>긍정 근거:</b>"), detail.index("<b>원문 요약:</b>"))
            window.shutdown()

    def test_detail_orders_ai_then_final_judgment_reason_and_core_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            item = StockNewsItem(
                "테스트기업 공급계약", "검색 요약", "https://example.com", "https://example.com",
                datetime.now(UTC), assess_stock_news("테스트기업", "테스트기업 공급계약", "검색 요약"),
            )
            stored = StoredAINewsAnalysis(
                AINewsAnalysis("AI 요약", "긍정", 87, "AI 판단 이유", ("수주",), (), "수주·계약"),
                "gemini", "test-model", datetime.now(UTC),
            )
            evidence = StoredNewsEvidence(
                identity=news_identity(item), title=item.title,
                article_revision_id="article-r1", body_revision_id="body-r1",
                body_status="fulltext", body_text="테스트기업이 공급계약을 체결했습니다.",
                core_sentences=("테스트기업이 공급계약을 체결했습니다.",),
            )
            window._visible_items = (item,)
            window._visible_groups = (NewsEventGroup(item, (item,)),)
            window._display_rows = (0,)
            window._ai_result_cache[news_identity(item)] = stored
            window._stock_code = "000001"
            key = window._evidence_key(item)
            window._evidence_cache[key] = (monotonic(), evidence)
            window._central_evidence_client = SimpleNamespace()

            window._show_detail(0)
            rendered = window._detail.toHtml()

            self.assertLess(rendered.index("AI 원문 분석"), rendered.index("최종 판단:"))
            self.assertLess(rendered.index("최종 판단:"), rendered.index("최종 판단 이유:"))
            self.assertLess(rendered.index("최종 판단 이유:"), rendered.index("AI 없이 뽑은 핵심 문장"))
            window.shutdown()

    def test_same_article_detail_refresh_restores_scroll_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            item = StockNewsItem(
                "테스트기업 실적 발표", "검색 요약", "https://example.com", "https://example.com",
                datetime.now(UTC), assess_stock_news("테스트기업", "테스트기업 실적 발표", "검색 요약"),
            )
            evidence = StoredNewsEvidence(
                identity=news_identity(item), title=item.title,
                article_revision_id="article-r1", body_revision_id="body-r1", body_status="fulltext",
                body_text=" ".join(f"테스트기업 본문 문장 {index}입니다." for index in range(300)),
            )
            window._visible_items = (item,)
            window._visible_groups = (NewsEventGroup(item, (item,)),)
            window._display_rows = (0,)
            window._stock_code = "000001"
            key = window._evidence_key(item)
            window._evidence_cache[key] = (monotonic(), evidence)
            window._central_evidence_client = SimpleNamespace()
            window.resize(700, 500)
            window.show()
            self.app.processEvents()

            window._show_detail(0)
            self.app.processEvents()
            scroll_bar = window._detail.verticalScrollBar()
            self.assertGreater(scroll_bar.maximum(), 0)
            expected = min(120, scroll_bar.maximum())
            scroll_bar.setValue(expected)

            window._show_detail(0)
            self.app.processEvents()

            self.assertEqual(expected, scroll_bar.value())
            window.shutdown()

    def test_rapid_stock_changes_keep_only_last_prepare_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            active_worker = SimpleNamespace(isRunning=lambda: True)
            window._prepare_worker = active_worker

            with patch.object(window, "refresh"):
                window.set_stock("005930", "삼성전자", activate=False)
                window.set_stock("000660", "SK하이닉스", activate=False)

            self.assertEqual((window._prepare_request_id, "000660"), window._pending_prepare)
            window._prepare_worker = None
            window.shutdown()

    def test_footer_omits_obsolete_browser_and_basic_rule_explanation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")

            notice = window._status_label.text()

            self.assertNotIn("기본 판단은 제목·요약 규칙", notice)
            self.assertNotIn("원문은 기본 브라우저에서 엽니다", notice)
            window.shutdown()

    def test_journal_sender_relay_receiver_persists_original_request_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
            canonical = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
            current_after_switch = AccountScope(
                "kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()),
            )
            request_documents: list[dict[str, object]] = []
            fill = TradeFill(
                "1", "005930", "삼성전자", "매수", datetime(2026, 9, 13, 9), 1, 70_000,
                origin_scope=origin, canonical_scope=canonical,
            )
            journal = SimpleNamespace(
                _active_episode=SimpleNamespace(
                    group_id="same-group", fills=(fill,),
                    summary=SimpleNamespace(
                        stock_code="005930", stock_name="삼성전자",
                        trade_date=date(2026, 9, 13), account_scope=canonical,
                    ),
                ),
                _journal_news_channel=SimpleNamespace(send=request_documents.append),
                _review_saved=SimpleNamespace(setText=lambda _value: None),
            )
            JournalWindow._open_active_news(journal)

            relayed: list[dict[str, object]] = []
            news_coordinator = NewsWindowCoordinator(
                root / "news.env", root / "news.sqlite3",
                lambda: QRect(10, 20, 800, 600), lambda _message: None, self.app,
            )
            news_coordinator.ensure_started = lambda: None
            news_coordinator._channel = SimpleNamespace(send=relayed.append)
            relay = SimpleNamespace(
                _journal_news_inbox=SimpleNamespace(read_new=lambda: request_documents[0]),
                _current_account_scope=current_after_switch,
                _news_window=news_coordinator,
            )
            MainWindow._poll_journal_news_request(relay)

            window = StockNewsWindow(root / "news.env", root / "news.sqlite3")
            with patch.object(window, "_schedule_prepare"):
                self.assertTrue(_apply_show_command(window, relayed[0]))
            item = StockNewsItem(
                "대표 기사", "요약", "https://n/1", "https://o/1", datetime.now(UTC),
                assess_stock_news("삼성전자", "대표 기사", "요약"),
            )
            window._repository.upsert("005930", (item,))
            window._visible_items = (item,)
            window._visible_groups = (NewsEventGroup(item, (item,)),)
            window._display_rows = (0,)
            window._table.setRowCount(1)
            window._table.setCurrentCell(0, 0)
            window._toggle_journal_link()

            self.assertEqual(origin, window._journal_origin_scope)
            self.assertEqual(canonical, window._journal_account_scope)
            self.assertEqual(
                {news_identity(item)},
                window._repository.journal_linked_identities("same-group", "005930", canonical),
            )
            self.assertEqual(
                set(), window._repository.journal_linked_identities(
                    "same-group", "005930", current_after_switch,
                ),
            )
            window.shutdown()

    def test_evidence_worker_keeps_only_latest_selection_and_rejects_stale_result(self) -> None:
        class ControlledWorker(QObject):
            completed = Signal(int, str, str, object)
            finished = Signal()
            instances: list["ControlledWorker"] = []

            def __init__(self, request_id, stock_code, item, _client, parent=None):
                super().__init__(parent)
                self.request_id, self.stock_code, self.item = request_id, stock_code, item
                self.started = False
                self.instances.append(self)

            def start(self): self.started = True
            def isRunning(self): return self.started
            def requestInterruption(self): self.started = False
            def wait(self, *_args): return True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            window._stock_code = "005930"
            window._central_evidence_client = SimpleNamespace()
            first = StockNewsItem(
                "첫 기사", "첫 요약", "https://n/1", "https://o/1", datetime.now(UTC),
                assess_stock_news("테스트기업", "첫 기사", "첫 요약"),
            )
            second = StockNewsItem(
                "둘째 기사", "둘째 요약", "https://n/2", "https://o/2", datetime.now(UTC),
                assess_stock_news("테스트기업", "둘째 기사", "둘째 요약"),
            )
            window._visible_items = (first, second)
            window._visible_groups = (
                NewsEventGroup(first, (first,)), NewsEventGroup(second, (second,)),
            )
            window._display_rows = (0, 1)
            window._table.setRowCount(2)

            with patch("kiwoom_monitor.presentation.stock_news_window.NewsEvidenceWorker", ControlledWorker):
                window._schedule_evidence(first)
                first_request = window._evidence_request_id
                window._table.blockSignals(True)
                window._table.setCurrentCell(1, 0)
                window._table.blockSignals(False)
                window._schedule_evidence(second)
                latest_request = window._evidence_request_id

                window._on_evidence_completed(
                    first_request, "005930", news_identity(first),
                    StoredNewsEvidence(news_identity(first), article_revision_id="stale"),
                )
                self.assertEqual({}, window._evidence_cache)
                self.assertEqual(news_identity(second), news_identity(window._pending_evidence[2]))

                ControlledWorker.instances[0].started = False
                ControlledWorker.instances[0].finished.emit()
                self.assertEqual(2, len(ControlledWorker.instances))
                window._on_evidence_completed(
                    latest_request, "005930", news_identity(second),
                    StoredNewsEvidence(news_identity(second), article_revision_id="current"),
                )
                self.assertEqual("current", window._cached_evidence(window._evidence_key(second)).article_revision_id)
                ControlledWorker.instances[1].started = False

            window._evidence_worker = None
            window.shutdown()

    def test_keyboard_current_cell_change_loads_evidence_for_new_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            first = StockNewsItem(
                "첫 기사", "요약", "https://n/1", "https://o/1", datetime.now(UTC),
                assess_stock_news("테스트기업", "첫 기사", "요약"),
            )
            window._visible_items = (first,)
            window._visible_groups = (NewsEventGroup(first, (first,)),)
            window._display_rows = (0,)
            window._table.setRowCount(1)

            with patch.object(window, "_schedule_evidence") as schedule:
                window._table.setCurrentCell(0, 1)

            schedule.assert_called_once_with(first)
            window.shutdown()

    def test_background_refresh_preserves_selected_article_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            first = StockNewsItem(
                "첫 기사", "첫 요약", "https://n/1", "https://o/1", datetime.now(UTC),
                assess_stock_news("테스트기업", "첫 기사", "첫 요약"),
            )
            selected = StockNewsItem(
                "선택 기사", "선택 요약", "https://n/2", "https://o/2", datetime.now(UTC),
                assess_stock_news("테스트기업", "선택 기사", "선택 요약"),
            )
            window._stock_code = "005930"
            window._central_evidence_client = SimpleNamespace()
            window._visible_items = (first, selected)
            window._visible_groups = (
                NewsEventGroup(first, (first,)), NewsEventGroup(selected, (selected,)),
            )
            window._render_items()
            self.app.processEvents()
            key = window._evidence_key(selected)
            window._evidence_cache[key] = (
                monotonic(),
                StoredNewsEvidence(
                    news_identity(selected), title=selected.title,
                    article_revision_id="article-2",
                    body_status="fulltext", body_text="새로고침 뒤에도 보존할 본문입니다.",
                ),
            )
            window._select_news_cell(1, 4)

            window._visible_items = (selected, first)
            window._visible_groups = (
                NewsEventGroup(selected, (selected,)), NewsEventGroup(first, (first,)),
            )
            window._render_items()
            self.app.processEvents()

            self.assertEqual(0, window._table.currentRow())
            self.assertEqual(news_identity(selected), window._selected_news_identity)
            self.assertIn("새로고침 뒤에도 보존할 본문입니다.", window._detail.toPlainText())
            window.shutdown()

    def test_shutdown_waits_for_blocking_evidence_request_and_discards_result(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingClient:
            def load_news_history(self, *_args, **_kwargs):
                entered.set()
                release.wait(2.0)
                return {"known": False, "revisions": []}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            item = StockNewsItem(
                "기사", "요약", "https://n/1", "https://o/1", datetime.now(UTC),
                assess_stock_news("테스트기업", "기사", "요약"),
            )
            worker = NewsEvidenceWorker(1, "005930", item, BlockingClient(), window)  # type: ignore[arg-type]
            completed: list[object] = []
            worker.completed.connect(lambda *_args: completed.append(_args))
            window._evidence_worker = worker
            worker.start()
            self.assertTrue(entered.wait(1.0))
            timer = threading.Timer(0.1, release.set)
            timer.start()

            window.shutdown()
            timer.join()

            self.assertFalse(worker.isRunning())
            self.assertEqual([], completed)

    def test_window_geometry_is_flushed_when_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            settings = QSettings(str(root / "window.ini"), QSettings.Format.IniFormat)
            window._window_settings = settings
            window.resize(845, 537)

            window._save_window_geometry()

            settings.sync()
            self.assertIsNotNone(settings.value("geometry"))
            window.shutdown()

    def test_automatic_analysis_does_not_change_selected_news(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            first = StockNewsItem(
                "첫 기사", "첫 내용", "https://example.com/1", "https://example.com/1",
                datetime.now(UTC), assess_stock_news("테스트기업", "첫 기사", "첫 내용"),
            )
            second = StockNewsItem(
                "둘째 기사", "둘째 내용", "https://example.com/2", "https://example.com/2",
                datetime.now(UTC), assess_stock_news("테스트기업", "둘째 기사", "둘째 내용"),
            )
            groups = (NewsEventGroup(first, (first,)), NewsEventGroup(second, (second,)))
            window._visible_items = (first, second)
            window._visible_groups = groups
            window._auto_ai_identities = {news_identity(second)}
            window._table.setRowCount(2)
            window._table.setCurrentCell(0, 4)

            with patch.object(window._config, "load_ai", return_value=NewsAISettings("gemini", "key", "model", 0, 10, True)), \
                    patch.object(window._ai_repository, "daily_count", return_value=0), \
                    patch.object(window._ai_repository, "load", return_value=None), \
                    patch.object(window, "_start_ai_groups") as start:
                window._auto_analyze_next()

            self.assertEqual(0, window._table.currentRow())
            start.assert_called_once_with((groups[1],), automatic=True)
            window.shutdown()

    def test_ai_finish_always_rechecks_last_stock_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            window._ai_continue = False
            window._ai_worker = None

            with patch.object(window, "_resume_auto_analysis") as resume:
                window._on_ai_finished()

            resume.assert_called_once_with()
            window.shutdown()

    def test_recent_candidates_are_recovered_after_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            first = StockNewsItem(
                "최신 기사", "내용", "https://example.com/1", "https://example.com/1",
                datetime.now(UTC), assess_stock_news("테스트기업", "최신 기사", "내용"),
            )
            second = StockNewsItem(
                "둘째 기사", "내용", "https://example.com/2", "https://example.com/2",
                datetime.now(UTC), assess_stock_news("테스트기업", "둘째 기사", "내용"),
            )
            window._visible_groups = (NewsEventGroup(first, (first,)), NewsEventGroup(second, (second,)))
            window._ai_result_cache = {news_identity(first): SimpleNamespace()}

            with patch.object(
                window._config, "load_ai",
                return_value=NewsAISettings("gemini", "key", "model", 0, 2, True),
            ):
                window._configure_recent_auto_candidates()

            self.assertEqual({news_identity(second)}, window._auto_ai_identities)
            window.shutdown()

    def test_legacy_ai_result_is_visible_without_automatic_bulk_reanalysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            item = StockNewsItem(
                "시장 전체 기사", "대상 종목은 단순 나열", "https://example.com/legacy",
                "https://example.com/legacy", datetime.now(UTC),
                assess_stock_news("테스트기업", "시장 전체 기사", "대상 종목은 단순 나열"),
            )
            identity = news_identity(item)
            window._visible_groups = (NewsEventGroup(item, (item,)),)
            window._ai_result_cache = {
                identity: StoredAINewsAnalysis(
                    AINewsAnalysis("기존 요약", "혼재", 50, "시장 전체 판단"),
                    "gemini", "model", datetime.now(UTC), "legacy-hash",
                )
            }

            with patch.object(
                window._config, "load_ai",
                return_value=NewsAISettings("gemini", "key", "model", 0, 1, True),
            ):
                window._configure_recent_auto_candidates()

            self.assertEqual(set(), window._auto_ai_identities)
            window.shutdown()

    def test_batch_mode_starts_multiple_events_in_one_worker_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            items = tuple(
                StockNewsItem(
                    f"기사 {index}", "내용", f"https://example.com/{index}", f"https://example.com/{index}",
                    datetime.now(UTC), assess_stock_news("테스트기업", f"기사 {index}", "내용"),
                ) for index in range(3)
            )
            groups = tuple(NewsEventGroup(item, (item,)) for item in items)
            window._visible_items = items
            window._visible_groups = groups
            window._auto_ai_identities = {news_identity(item) for item in items}

            with patch.object(
                window._config, "load_ai",
                return_value=NewsAISettings("gemini", "key", "model", 0, 3, True, "batch", 3),
            ), patch.object(window, "_start_ai_groups") as start:
                window._auto_analyze_next()

            start.assert_called_once_with(groups, automatic=True)
            self.assertFalse(window._auto_ai_identities)
            window.shutdown()

    def test_automatic_analysis_starts_with_newest_visible_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            old = StockNewsItem(
                "과거 기사", "내용", "https://example.com/old", "https://example.com/old",
                datetime(2026, 8, 27, tzinfo=UTC), assess_stock_news("테스트기업", "과거 기사", "내용"),
            )
            newest = StockNewsItem(
                "최신 기사", "내용", "https://example.com/new", "https://example.com/new",
                datetime(2026, 8, 29, tzinfo=UTC), assess_stock_news("테스트기업", "최신 기사", "내용"),
            )
            groups = (NewsEventGroup(newest, (newest,)), NewsEventGroup(old, (old,)))
            window._visible_items = (newest, old)
            window._visible_groups = groups
            window._auto_ai_identities = {news_identity(newest), news_identity(old)}

            with patch.object(
                window._config, "load_ai",
                return_value=NewsAISettings("gemini", "key", "model", 0, 2, True, "single", 2),
            ), patch.object(window, "_start_ai_groups") as start:
                window._auto_analyze_next()

            start.assert_called_once_with((groups[0],), automatic=True)
            window.shutdown()


if __name__ == "__main__":
    unittest.main()
