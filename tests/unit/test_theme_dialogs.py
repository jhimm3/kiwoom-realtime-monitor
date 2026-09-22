from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.application.theme_preview import ThemeChange
from kiwoom_monitor.application.theme_suggestions import ProfileThemeSuggestion
from kiwoom_monitor.presentation.theme_dialogs import ThemePreviewDialog, ThemeSuggestionReviewDialog


class ThemePreviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_edited_changes_are_returned_without_name_error(self) -> None:
        original = ThemeChange("005930", "삼성전자", ("반도체",), ("AI",), "테마 변경")
        dialog = ThemePreviewDialog((original,), 0)

        changes = dialog.changes(",")

        self.assertEqual(1, len(changes))
        self.assertEqual(("반도체", "AI"), changes[0].after)
        self.assertEqual("테마 추가", changes[0].status)
        dialog.close()

    def test_theme_suggestion_review_returns_edited_approval_and_rejection(self) -> None:
        approved = ProfileThemeSuggestion(
            "005930", "삼성전자", "news-1", "호남클러스터", ("호남클러스터",),
            "복수 기업 참여", 85, "pending", "openai", "model", datetime.now(UTC),
        )
        rejected = ProfileThemeSuggestion(
            "000660", "SK하이닉스", "news-2", "주가상승", ("주가상승",),
            "주가 움직임만 언급", 20, "pending", "openai", "model", datetime.now(UTC),
        )
        dialog = ThemeSuggestionReviewDialog((approved, rejected))
        dialog._table.item(0, dialog.TARGET_COLUMN).setText("호남개발")
        dialog._table.cellWidget(0, dialog.ACTION_COLUMN).setCurrentIndex(1)
        dialog._table.cellWidget(1, dialog.ACTION_COLUMN).setCurrentIndex(2)

        decisions = dialog.decisions()

        self.assertEqual((approved, "approved", ("호남개발",)), decisions[0])
        self.assertEqual((rejected, "rejected", ()), decisions[1])
        dialog.close()

    def test_unchanged_target_is_re_resolved_when_an_earlier_row_updates_alias(self) -> None:
        first = ProfileThemeSuggestion(
            "000001", "첫 종목", "news-1", "호남클러스터", ("호남클러스터",),
            "첫 근거", 80, "pending", "openai", "model", datetime.now(UTC),
        )
        second = ProfileThemeSuggestion(
            "000002", "둘째 종목", "news-2", "호남클러스터", ("호남클러스터",),
            "둘째 근거", 75, "pending", "openai", "model", datetime.now(UTC),
        )
        dialog = ThemeSuggestionReviewDialog((first, second))
        dialog._table.item(0, dialog.TARGET_COLUMN).setText("호남개발")
        dialog._table.cellWidget(0, dialog.ACTION_COLUMN).setCurrentIndex(1)
        dialog._table.cellWidget(1, dialog.ACTION_COLUMN).setCurrentIndex(1)

        decisions = dialog.decisions()

        self.assertEqual(("호남개발",), decisions[0][2])
        self.assertEqual((), decisions[1][2])
        dialog.close()


if __name__ == "__main__":
    unittest.main()
