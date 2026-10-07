"""메인 순위표 열 설정과 자동 맞춤 상태를 조정한다."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from kiwoom_monitor.infrastructure.persistence.column_settings_repository import (
    ColumnSetting,
    ColumnSettingsRepository,
)
from kiwoom_monitor.presentation.main_window_layout import proportional_column_widths


class MainTableColumnController:
    """열 저장소와 표 위젯 사이의 표시·순서·폭 계약을 소유한다."""

    def __init__(
        self,
        table: Any,
        repository: ColumnSettingsRepository | None,
        columns: tuple[tuple[str, str], ...],
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._table = table
        self._repository = repository
        self._columns = columns
        self._now = now
        self._resizing = False
        self._restoring = False
        self._auto_fit_ready = False
        self._manual_resize_until = 0.0

    @property
    def manual_size_hold(self) -> bool:
        return self._now() < self._manual_resize_until

    @property
    def auto_fit_ready(self) -> bool:
        return self._auto_fit_ready

    def restore(self) -> bool:
        if self._repository is None:
            return False
        saved = {setting.name: setting for setting in self._repository.list()}
        header = self._table.horizontalHeader()
        before = tuple(
            (not self._table.isColumnHidden(logical), header.visualIndex(logical))
            for logical, _ in enumerate(self._columns)
        )
        self._restoring = True
        try:
            for logical, (name, _) in enumerate(self._columns):
                setting = saved.get(name)
                if setting is not None:
                    self._table.setColumnHidden(logical, not setting.visible)
                    header.resizeSection(logical, setting.width)
            names = [name for name, _ in self._columns]
            for target, setting in enumerate(sorted(saved.values(), key=lambda item: item.position)):
                if setting.name in names:
                    logical = names.index(setting.name)
                    header.moveSection(header.visualIndex(logical), target)
        finally:
            self._restoring = False
        after = tuple(
            (not self._table.isColumnHidden(logical), header.visualIndex(logical))
            for logical, _ in enumerate(self._columns)
        )
        return before != after

    def save(self) -> None:
        if self._repository is None or self._restoring or self._resizing:
            return
        header = self._table.horizontalHeader()
        settings = tuple(
            ColumnSetting(
                name,
                not self._table.isColumnHidden(logical),
                header.visualIndex(logical),
                header.sectionSize(logical),
            )
            for logical, (name, _) in enumerate(self._columns)
        )
        self._repository.save(settings)

    def section_resized(self) -> None:
        if self._resizing or self._restoring:
            return
        self.hold_manual_size()
        self.save()

    def hold_manual_size(self, seconds: float = 30.0) -> None:
        self._manual_resize_until = self._now() + seconds

    def enable_initial_auto_fit(self) -> None:
        self._auto_fit_ready = True

    def resize_proportionally(self, *, responsive: bool) -> bool:
        if not self._auto_fit_ready or not responsive or self.manual_size_hold:
            return False
        visible = tuple(
            index
            for index in range(self._table.columnCount())
            if not self._table.isColumnHidden(index)
        )
        if not visible:
            return False
        current = tuple(self._table.columnWidth(index) for index in visible)
        widths = proportional_column_widths(current, self._table.viewport().width())
        if widths == current:
            return False
        self._resizing = True
        try:
            for index, width in zip(visible, widths):
                self._table.setColumnWidth(index, width)
        finally:
            self._resizing = False
        return True

    def set_visible(self, index: int, visible: bool, *, responsive: bool) -> None:
        self._table.setColumnHidden(index, not visible)
        self._table.updateGeometry()
        self._table.horizontalHeader().viewport().update()
        self.resize_proportionally(responsive=responsive)
        self.save()

    def auto_fit(self, *, responsive: bool) -> None:
        self._manual_resize_until = 0.0
        self._resizing = True
        try:
            self._table.resizeColumnsToContents()
        finally:
            self._resizing = False
        self.resize_proportionally(responsive=responsive)
        self.save()

    def reset(self) -> bool:
        if self._repository is None:
            return False
        self._repository.reset()
        self._manual_resize_until = 0.0
        return self.restore()
