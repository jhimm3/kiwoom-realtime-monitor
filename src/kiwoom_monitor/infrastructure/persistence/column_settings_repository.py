from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from kiwoom_monitor.domain.time_basis import kst_now, as_kst

@dataclass(frozen=True)
class ColumnSetting:
    name: str
    visible: bool
    position: int
    width: int

class ColumnSettingsRepository:
    def __init__(
        self, database_path: Path, *, now_provider=None,
        column_names: tuple[str, ...] | None = None, strict_values: bool = False,
    ) -> None:
        self._database_path = database_path
        self.set_now_provider(now_provider or kst_now)
        self._column_names = column_names
        self._strict_values = strict_values

    def set_now_provider(self, now_provider) -> None:
        self._now = lambda: as_kst(now_provider())

    def list(self) -> tuple[ColumnSetting, ...]:
        con = sqlite3.connect(self._database_path)
        try:
            statement = "SELECT column_name, visible, position, width FROM column_settings"
            parameters = ()
            if self._column_names is not None:
                statement += " WHERE column_name IN (" + ",".join("?" for _ in self._column_names) + ")"
                parameters = self._column_names
            rows = con.execute(statement + " ORDER BY position", parameters).fetchall()
        finally:
            con.close()
        if self._strict_values:
            missing = set(self._column_names or ()) - {row[0] for row in rows}
            if missing:
                raise ValueError("조회 열 설정 자료가 없습니다: " + ", ".join(sorted(missing)))
            for name, visible, position, width in rows:
                if (type(name) is not str or type(visible) is not int or visible not in (0, 1)
                        or type(position) is not int or type(width) is not int
                        or position < 0 or width < 0):
                    raise ValueError(f"조회 열 설정 '{name}'의 원자료를 적용할 수 없습니다.")
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
