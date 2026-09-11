from __future__ import annotations

from datetime import datetime
from PySide6.QtCore import QThread, Signal

from .stock_catalog import fetch_krx_stock_catalog


class KrxStockCatalogWorker(QThread):
    completed = Signal(int, bool)
    failed = Signal(str)
    history_failed = Signal(str)
    def __init__(self, stocks: object, settings: object) -> None:
        super().__init__(); self._stocks = stocks; self._settings = settings

    def run(self) -> None:
        now = datetime.now()
        try:
            rows = fetch_krx_stock_catalog(timeout=30)
            if self.isInterruptionRequested():
                return
            self._stocks.upsert_many(rows); self._settings.set("krx_stock_catalog_date", now.strftime("%Y-%m-%d %H:%M:%S")); self._settings.set("krx_stock_catalog_format_version", "3")
            sync_history = getattr(self._stocks, "sync_kind_name_history", None)
            if callable(sync_history) and not self.isInterruptionRequested():
                try:
                    initial = not bool(self._settings.get("kind_name_history_sync_date"))
                    sync_history(initial=initial)
                    self._settings.set("kind_name_history_sync_date", now.strftime("%Y-%m-%d %H:%M:%S"))
                except Exception as error:
                    self.history_failed.emit(str(error))
            if not self.isInterruptionRequested():
                self.completed.emit(len(rows), False)
        except Exception as error:
            self.failed.emit(str(error))
