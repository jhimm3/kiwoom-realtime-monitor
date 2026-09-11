from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting
from kiwoom_monitor.presentation.main_table_column_controller import MainTableColumnController


class FakeRepository:
    def __init__(self, settings: tuple[ColumnSetting, ...]) -> None:
        self.settings = settings
        self.saved: list[tuple[ColumnSetting, ...]] = []
        self.reset_count = 0

    def list(self) -> tuple[ColumnSetting, ...]:
        return self.settings

    def save(self, settings: tuple[ColumnSetting, ...]) -> None:
        self.saved.append(settings)

    def reset(self) -> None:
        self.reset_count += 1


class FakeViewport:
    def __init__(self, width: int) -> None:
        self._width = width
        self.updated = False

    def width(self) -> int:
        return self._width

    def update(self) -> None:
        self.updated = True


class FakeHeader:
    def __init__(self, table: "FakeTable") -> None:
        self._table = table
        self._visual = list(range(len(table.widths)))
        self._viewport = FakeViewport(0)

    def visualIndex(self, logical: int) -> int:
        return self._visual.index(logical)

    def moveSection(self, source: int, target: int) -> None:
        self._visual.insert(target, self._visual.pop(source))

    def resizeSection(self, logical: int, width: int) -> None:
        self._table.widths[logical] = width

    def sectionSize(self, logical: int) -> int:
        return self._table.widths[logical]

    def viewport(self) -> FakeViewport:
        return self._viewport


class FakeTable:
    def __init__(self, widths: list[int], viewport_width: int = 600) -> None:
        self.widths = widths
        self.hidden = [False] * len(widths)
        self._viewport = FakeViewport(viewport_width)
        self._header = FakeHeader(self)
        self.geometry_updates = 0
        self.auto_fit_count = 0

    def horizontalHeader(self) -> FakeHeader:
        return self._header

    def viewport(self) -> FakeViewport:
        return self._viewport

    def columnCount(self) -> int:
        return len(self.widths)

    def isColumnHidden(self, index: int) -> bool:
        return self.hidden[index]

    def setColumnHidden(self, index: int, hidden: bool) -> None:
        self.hidden[index] = hidden

    def columnWidth(self, index: int) -> int:
        return self.widths[index]

    def setColumnWidth(self, index: int, width: int) -> None:
        self.widths[index] = width

    def updateGeometry(self) -> None:
        self.geometry_updates += 1

    def resizeColumnsToContents(self) -> None:
        self.auto_fit_count += 1
        self.widths = [50] * len(self.widths)


class MainTableColumnControllerTests(unittest.TestCase):
    COLUMNS = (("rank", "순위"), ("name", "종목명"), ("theme", "테마"))

    def test_restore_applies_visibility_width_and_order(self) -> None:
        table = FakeTable([100, 100, 100])
        repository = FakeRepository((
            ColumnSetting("name", True, 0, 140),
            ColumnSetting("rank", False, 1, 80),
            ColumnSetting("theme", True, 2, 180),
        ))
        controller = MainTableColumnController(table, repository, self.COLUMNS)

        self.assertTrue(controller.restore())

        self.assertEqual([80, 140, 180], table.widths)
        self.assertEqual([True, False, False], table.hidden)
        self.assertEqual([1, 0, 2], table._header._visual)

    def test_save_preserves_logical_name_and_visual_position(self) -> None:
        table = FakeTable([80, 140, 180])
        table.hidden[2] = True
        table._header._visual = [1, 0, 2]
        repository = FakeRepository(())
        controller = MainTableColumnController(table, repository, self.COLUMNS)

        controller.save()

        self.assertEqual(
            (
                ColumnSetting("rank", True, 1, 80),
                ColumnSetting("name", True, 0, 140),
                ColumnSetting("theme", False, 2, 180),
            ),
            repository.saved[-1],
        )

    def test_restore_signal_does_not_persist_intermediate_column_order(self) -> None:
        table = FakeTable([80, 140, 180])
        repository = FakeRepository(())
        controller = MainTableColumnController(table, repository, self.COLUMNS)
        controller._restoring = True

        controller.save()

        self.assertEqual([], repository.saved)

    def test_manual_resize_holds_proportional_resize_for_thirty_seconds(self) -> None:
        clock = [100.0]
        table = FakeTable([100, 200, 300], 1_200)
        repository = FakeRepository(())
        controller = MainTableColumnController(table, repository, self.COLUMNS, lambda: clock[0])
        controller.enable_initial_auto_fit()

        controller.section_resized()
        self.assertFalse(controller.resize_proportionally(responsive=True))
        self.assertEqual([100, 200, 300], table.widths)
        clock[0] = 131.0
        self.assertTrue(controller.resize_proportionally(responsive=True))
        self.assertEqual([200, 400, 600], table.widths)
        self.assertEqual(1, len(repository.saved))

    def test_visibility_auto_fit_and_reset_delegate_to_table_and_repository(self) -> None:
        table = FakeTable([100, 200, 300], 600)
        repository = FakeRepository(())
        controller = MainTableColumnController(table, repository, self.COLUMNS)
        controller.enable_initial_auto_fit()

        controller.set_visible(2, False, responsive=True)
        controller.auto_fit(responsive=True)
        controller.reset()

        self.assertTrue(table.hidden[2])
        self.assertEqual(1, table.geometry_updates)
        self.assertTrue(table._header._viewport.updated)
        self.assertEqual(1, table.auto_fit_count)
        self.assertEqual(1, repository.reset_count)
        self.assertEqual(2, len(repository.saved))


if __name__ == "__main__":
    unittest.main()
