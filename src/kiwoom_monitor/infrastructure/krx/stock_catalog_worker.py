from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import QThread, Signal

from kiwoom_monitor.domain.time_basis import as_kst, kst_wall_now

from .stock_catalog import fetch_krx_stock_catalog


class KrxStockCatalogWorker(QThread):
    completed = Signal(int, bool)
    failed = Signal(str)
    history_failed = Signal(str)
    def __init__(self, stocks: object, settings: object) -> None:
        super().__init__(); self._stocks = stocks; self._settings = settings
        self._now: Callable[[], datetime] | None = None

    def set_now_provider(self, provider: Callable[[], datetime]) -> None:
        self._now = provider

    def run(self) -> None:
        try:
            now = as_kst((self._now or kst_wall_now)())
            stamp = now.strftime("%Y-%m-%d %H:%M:%S")
            load_premarket = getattr(self._stocks, "load_premarket_flags", None)
            use_premarket = callable(load_premarket) and load_premarket(now.date().isoformat()) is not None
            count = 0
            if not use_premarket:
                rows = fetch_krx_stock_catalog(timeout=30)
                if self.isInterruptionRequested():
                    return
                self._stocks.upsert_many(rows)
                self._settings.set("krx_stock_catalog_date", stamp)
                self._settings.set("krx_stock_catalog_format_version", "3")
                count = len(rows)
            sync_history = getattr(self._stocks, "sync_kind_name_history", None)
            if callable(sync_history) and not self.isInterruptionRequested():
                try:
                    initial = not bool(self._settings.get("kind_name_history_sync_date", ""))
                    sync_history(initial=initial)
                    self._settings.set("kind_name_history_sync_date", stamp)
                except Exception as error:
                    self.history_failed.emit(str(error))
            if not self.isInterruptionRequested():
                self.completed.emit(count, use_premarket)
        except Exception as error:
            self.failed.emit(str(error))
