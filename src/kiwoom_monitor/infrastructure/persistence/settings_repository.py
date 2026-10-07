from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class SettingsRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._cache: dict[str, str] = {}

    @property
    def database_path(self) -> Path:
        return self._database_path

    def get(self, key: str) -> str:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        connection = sqlite3.connect(self._database_path)
        try:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(key)
        value = str(row[0])
        self._cache[key] = value
        return value

    def set(self, key: str, value: str) -> None:
        self.set_many({key: value})

    def set_many(self, values: dict[str, str]) -> None:
        """Save one settings form in one transaction, then expose its values to the UI."""
        if not values:
            return
        updated_at = datetime.now(UTC).isoformat()
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS central_setting_versions ("
                "setting_key TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                tuple(values.items()),
            )
            connection.executemany(
                "INSERT INTO central_setting_versions(setting_key,updated_at) VALUES(?,?) "
                "ON CONFLICT(setting_key) DO UPDATE SET updated_at=excluded.updated_at",
                tuple((key, updated_at) for key in values),
            )
            connection.commit()
        finally:
            connection.close()
        self._cache.update(values)

    def clear_cache(self) -> None:
        self._cache.clear()
