from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ColumnSetting:
    name: str
    visible: bool
    position: int
    width: int

class ColumnSettingsRepository:
    def __init__(self, database_path: Path) -> None: self._database_path = database_path
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
            con.executemany("UPDATE column_settings SET visible=?, position=?, width=? WHERE column_name=?", [(int(s.visible),s.position,s.width,s.name) for s in settings])
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
            con.commit()
        finally:
            con.close()
