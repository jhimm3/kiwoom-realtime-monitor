from __future__ import annotations

import unittest

from kiwoom_monitor.presentation.main_window_layout import (
    clamp_uniform_row_height,
    fitted_window_size,
    parse_window_position,
    proportional_column_widths,
    responsive_row_height,
)


class MainWindowLayoutTests(unittest.TestCase):
    def test_column_ui_native_noop_reopen_and_explicit_reset(self) -> None:
        import gc
        import os
        import sqlite3
        import tempfile
        from contextlib import closing
        from datetime import datetime, timedelta
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication, QEvent, Qt
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.domain.time_basis import KST
        from kiwoom_monitor.infrastructure.persistence.column_settings_repository import (
            ColumnSetting, ColumnSettingsRepository,
        )
        from kiwoom_monitor.infrastructure.persistence.database import Database, DEFAULT_COLUMNS
        from kiwoom_monitor.presentation.column_manager_dialog import ColumnManagerDialog
        from kiwoom_monitor.presentation.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        # Other Qt modules can share this worker, so keep the singleton alive.
        type(self)._qt_app = app
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            database.settings.set("ui_mode", "fixed")
            now = [datetime(2030, 1, 2, 10, tzinfo=KST)]
            loader = SimpleNamespace(load_top_stocks=lambda: (), server_now=lambda: now[0])
            repository = ColumnSettingsRepository(path, now_provider=lambda: now[0])
            windows = []

            def versions() -> dict[str, str]:
                with closing(sqlite3.connect(path)) as connection:
                    return dict(connection.execute(
                        "SELECT column_name, updated_at FROM central_column_setting_versions"
                    ))

            def assert_table_matches(window: MainWindow) -> None:
                header = window._table.horizontalHeader()
                by_name = {setting.name: setting for setting in repository.list()}
                for logical, (name, _) in enumerate(window.COLUMNS):
                    setting = by_name[name]
                    self.assertEqual(setting.visible, not window._table.isColumnHidden(logical))
                    self.assertEqual(setting.position, header.visualIndex(logical))
                    self.assertEqual(setting.width, header.sectionSize(logical))

            try:
                window = MainWindow(database.settings, loader, columns=repository)
                windows.append(window)
                app.processEvents()
                header = window._table.horizontalHeader()
                # Real header signals invoke MainWindow -> controller -> native save.
                header.moveSection(header.visualIndex(0), 2)
                before_resize = versions()
                now[0] += timedelta(seconds=1)
                header.resizeSection(1, 175)
                after_resize = versions()
                self.assertEqual(now[0].isoformat(), after_resize["stock"])
                self.assertEqual(
                    {key: value for key, value in before_resize.items() if key != "stock"},
                    {key: value for key, value in after_resize.items() if key != "stock"},
                )
                window._set_column_visible(2, False)
                app.processEvents()
                self.assertTrue(window._table.isColumnHidden(2))
                # The embedded settings panel uses its real itemChanged -> save -> apply path.
                panel = ColumnManagerDialog(repository, window.COLUMNS, window._table, window,
                                            embedded=True, on_applied=window._apply_column_settings)
                item = next(panel._list.item(row) for row in range(panel._list.count())
                            if panel._list.item(row).data(Qt.ItemDataRole.UserRole) == 3)
                item.setCheckState(Qt.CheckState.Unchecked)
                app.processEvents()
                self.assertTrue(window._table.isColumnHidden(3))
                window._save_columns()
                assert_table_matches(window)
                saved = repository.list()
                receipt = versions()
                now[0] += timedelta(seconds=1)
                window._save_columns()
                self.assertEqual(saved, repository.list())
                self.assertEqual(receipt, versions())
                with patch.object(window, "_save_columns", wraps=window._save_columns) as close_save:
                    self.assertTrue(window.close())
                    close_save.assert_called_once_with()
                self.assertEqual(receipt, versions())
                restored = MainWindow(database.settings, loader, columns=repository)
                windows.append(restored)
                app.processEvents()
                assert_table_matches(restored)
                self.assertEqual(receipt, versions())
                defaults = tuple(ColumnSetting(name, bool(visible), position, width)
                                 for name, visible, position, width in DEFAULT_COLUMNS)
                for _ in range(2):
                    now[0] += timedelta(seconds=1)
                    restored._reset_columns()
                    app.processEvents()
                    self.assertEqual(defaults, repository.list())
                    assert_table_matches(restored)
                    self.assertEqual({setting.name: now[0].isoformat() for setting in defaults}, versions())
            finally:
                for window in windows:
                    app.processEvents()
                    window.close()
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
                app.processEvents()
                gc.collect()

    def test_row_height_limits_match_existing_ui_contract(self) -> None:
        self.assertEqual(12, clamp_uniform_row_height(5))
        self.assertEqual(42, clamp_uniform_row_height(42))
        self.assertEqual(100, clamp_uniform_row_height(120))

    def test_responsive_row_height_uses_viewport_and_limits(self) -> None:
        self.assertEqual(30, responsive_row_height(20, 600, 24))
        self.assertEqual(14, responsive_row_height(20, 100, 24))
        self.assertEqual(64, responsive_row_height(1, 200, 24))
        self.assertEqual(24, responsive_row_height(0, 0, 24))

    def test_proportional_widths_preserve_available_total(self) -> None:
        self.assertEqual((100, 200, 300), proportional_column_widths((50, 100, 150), 600))

    def test_proportional_widths_keep_minimum_and_noop_conditions(self) -> None:
        self.assertEqual((), proportional_column_widths((), 400))
        self.assertEqual((100, 200), proportional_column_widths((100, 200), 300))
        self.assertEqual((40, 80), proportional_column_widths((10, 90), 120))

    def test_fitted_window_size_respects_minimum(self) -> None:
        self.assertEqual(
            (900, 700),
            fitted_window_size(
                window_width=800,
                window_height=600,
                minimum_width=500,
                minimum_height=400,
                table_width=600,
                table_height=400,
                content_width=700,
                content_height=500,
            ),
        )
        self.assertEqual(
            (500, 400),
            fitted_window_size(
                window_width=500,
                window_height=400,
                minimum_width=500,
                minimum_height=400,
                table_width=700,
                table_height=600,
                content_width=100,
                content_height=100,
            ),
        )

    def test_parse_window_position_distinguishes_invalid_values(self) -> None:
        self.assertEqual((10, -20), parse_window_position("10", "-20"))
        self.assertIsNone(parse_window_position("", "20"))
        self.assertIsNone(parse_window_position(None, "20"))


if __name__ == "__main__":
    unittest.main()
