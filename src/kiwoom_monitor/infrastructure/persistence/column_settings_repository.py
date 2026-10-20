from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from kiwoom_monitor.domain.time_basis import kst_now, as_kst

@dataclass(frozen=True)
class ColumnSetting:
    name: str
    visible: bool
    position: int
    width: int

class ColumnSettingsRepository:
    def __init__(self, database_path: Path, *, now_provider=None) -> None:
        self._database_path = database_path
        self._now = (lambda: as_kst(now_provider())) if now_provider else kst_now
    def list(self) -> tuple[ColumnSetting, ...]:
        con = sqlite3.connect(self._database_path)
        try:
            rows = con.execute("SELECT column_name, visible, position, width FROM column_settings ORDER BY position").fetchall()
        finally:
            con.close()
        return tuple(ColumnSetting(str(n), bool(v), int(p), int(w)) for n,v,p,w in rows)
    def save(self, settings: tuple[ColumnSetting, ...]) -> None:
        con = sqlite3.connect(self._database_path)
        try:
            # Preserve writer-lock-before-clock and empty-save DDL rollback boundaries.
            con.execute("BEGIN IMMEDIATE" if settings else "BEGIN")
            self._record_versions(con, tuple(setting.name for setting in settings), settings=settings)
            con.executemany(
                "UPDATE column_settings SET visible=?, position=?, width=? WHERE column_name=? "
                "AND (visible IS NOT ? OR position IS NOT ? OR width IS NOT ?)",
                [(int(s.visible), s.position, s.width, s.name, int(s.visible), s.position, s.width) for s in settings],
            )
            con.commit()
        finally:
            con.close()

    def reset(self) -> None:
        from kiwoom_monitor.infrastructure.persistence.database import DEFAULT_COLUMNS

        con = sqlite3.connect(self._database_path)
        try:
            con.executemany(
                "UPDATE column_settings SET visible=?, position=?, width=? WHERE column_name=?",
                [(visible, position, width, name) for name, visible, position, width in DEFAULT_COLUMNS],
            )
            self._record_versions(con, tuple(name for name, *_ in DEFAULT_COLUMNS))
            con.commit()
        finally:
            con.close()

    def _record_versions(
        self, connection: sqlite3.Connection, names: tuple[str, ...],
        *, settings: tuple[ColumnSetting, ...] | None = None,
    ) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS central_column_setting_versions ("
            "column_name TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
        )
        now = self._now().isoformat()
        statement = (
            "INSERT INTO central_column_setting_versions(column_name,updated_at) VALUES(?,?) "
            "ON CONFLICT(column_name) DO UPDATE SET updated_at=excluded.updated_at"
        )
        if settings is None:
            parameters = ((name, now) for name in names)
        else:
            # Compare before the value UPDATE, within this UPSERT's writer transaction.
            # Missing versions and unknown names retain their existing registration contract.
            statement += (
                " WHERE NOT EXISTS (SELECT 1 FROM column_settings "
                "WHERE column_name=excluded.column_name AND visible IS ? AND position IS ? AND width IS ?)"
            )
            parameters = ((s.name, now, int(s.visible), s.position, s.width) for s in settings)
        connection.executemany(statement, parameters)
