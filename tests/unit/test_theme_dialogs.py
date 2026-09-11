from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.application.theme_preview import ThemeChange
from kiwoom_monitor.presentation.theme_dialogs import ThemePreviewDialog


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


if __name__ == "__main__":
    unittest.main()
