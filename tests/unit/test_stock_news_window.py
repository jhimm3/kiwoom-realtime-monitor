from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from kiwoom_monitor.presentation.stock_news_window import StockNewsWindow
from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import NewsAISettings, StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis
from kiwoom_monitor.application.news_grouping import NewsEventGroup
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import news_identity


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
                ["independent", "linked", "docked"],
                [window._window_mode.itemData(index) for index in range(window._window_mode.count())],
            )
            window._apply_window_mode("docked")
            self.assertEqual("docked", window._window_settings.value("window_mode"))

            window.shutdown()

    def test_judgment_double_click_starts_ai_and_title_double_click_opens_article(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = StockNewsWindow(root / "news.env", root / "monitor.sqlite3")
            window._window_settings = QSettings(str(root / "window.ini"), QSettings.Format.IniFormat)

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
                    patch.object(window, "_start_ai_analysis") as start:
                window._auto_analyze_next()

            self.assertEqual(0, window._table.currentRow())
            start.assert_called_once_with(groups[1], automatic=True)
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


if __name__ == "__main__":
    unittest.main()
