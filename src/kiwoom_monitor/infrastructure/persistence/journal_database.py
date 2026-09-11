"""매매일지 전용 SQLite 저장소."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.journal_bar_repository import (
    BarBackfillState,
    JournalBarRepositoryMixin,
)
from kiwoom_monitor.infrastructure.persistence.journal_schema import initialize_journal_database
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
    JournalSnapshotRepositoryMixin,
    TradeEntrySnapshot,
)
from kiwoom_monitor.infrastructure.persistence.journal_strategy_settings_repository import (
    JournalStrategySettingsRepositoryMixin,
)
from kiwoom_monitor.infrastructure.persistence.journal_trade_repository import JournalTradeRepositoryMixin


class JournalRepository(
    JournalSnapshotRepositoryMixin,
    JournalStrategySettingsRepositoryMixin,
    JournalTradeRepositoryMixin,
    JournalBarRepositoryMixin,
):
    def __init__(self, path: Path) -> None:
        self._path = path
        initialize_journal_database(path)

    def remember_stock(self, code: str, name: str, now: datetime) -> None:
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                "INSERT INTO journal_stocks VALUES (?, ?, ?, 'opened', ?) "
                "ON CONFLICT(trade_date, stock_code) DO UPDATE SET stock_name=excluded.stock_name, last_opened_at=excluded.last_opened_at",
                    (now.date().isoformat(), code, name, now.isoformat(timespec="seconds")),
                )
