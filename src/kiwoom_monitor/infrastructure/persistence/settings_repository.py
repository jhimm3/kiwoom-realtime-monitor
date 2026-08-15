from __future__ import annotations

import sqlite3
from pathlib import Path


class SettingsRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    @property
    def database_path(self) -> Path:
        return self._database_path

    def get(self, key: str) -> str:
        connection = sqlite3.connect(self._database_path)
        try:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(key)
        return str(row[0])

    def set(self, key: str, value: str) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            connection.commit()
        finally:
            connection.close()
