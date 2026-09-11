from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.application.strategy_pack import MIMOSA_MANIFEST, StrategyPackManifest
from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft, StrategyRuleDraft
from kiwoom_monitor.presentation.strategy_pack_dialogs import StrategyPackManagerDialog
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository


class StrategyPackDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_draft_cannot_be_enabled_before_review(self) -> None:
        draft = StrategyPackManifest(
            "course_pullback", "눌림 강의", 1, True, 100, ("눌림",), ("1분봉",),
            source_files=("lecture.pdf",), review_status="draft",
        )
        with tempfile.TemporaryDirectory() as directory:
            dialog = StrategyPackManagerDialog((MIMOSA_MANIFEST, draft), JournalRepository(Path(directory) / "journal.db"))
            packs = dialog.packs()
            self.assertTrue(packs[0].enabled)
            self.assertFalse(packs[1].enabled)
            self.assertEqual("draft", packs[1].review_status)
            dialog.close()

    def test_reviewed_mapped_pack_can_be_approved_then_enabled(self) -> None:
        pack = StrategyPackManifest(
            "course_pullback", "눌림 강의", 1, False, 100, ("눌림",), ("분봉", "거래대금"),
            source_files=("lecture.txt",), review_status="rules_reviewed",
        )
        draft = ExtractedStrategyDraft((
            StrategyRuleDraft("진입 조건", "고점 대비 4% 눌림", "x", "", ("분봉",), True, "pullback_from_session_high_pct", ">=", 4.0),
        ), {})
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.db")
            repository.save_strategy_pack_draft(pack.pack_id, draft)
            dialog = StrategyPackManagerDialog((MIMOSA_MANIFEST, pack), repository)
            dialog.table.selectRow(1); dialog._approve()
            dialog.table.item(1, 0).setCheckState(dialog.table.item(0, 0).checkState())
            saved = dialog.packs()[1]
            self.assertEqual("approved", saved.review_status)
            self.assertTrue(saved.enabled)
            dialog.close()


if __name__ == "__main__":
    unittest.main()
