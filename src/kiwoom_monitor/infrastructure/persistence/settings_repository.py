from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock
from kiwoom_monitor.domain.time_basis import kst_now, as_kst


class SettingsRepository:
    def __init__(self, database_path: Path, *, now_provider=None) -> None:
        self._database_path = database_path
        self._now = (lambda: as_kst(now_provider())) if now_provider else kst_now
        self._cache: dict[str, str] = {}
        self._cache_lock = Lock()
        self._cache_generation = 0

    @property
    def database_path(self) -> Path:
        return self._database_path

    def get(self, key: str) -> str:
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            generation = self._cache_generation
        connection = sqlite3.connect(self._database_path)
        try:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(key)
        value = str(row[0])
        with self._cache_lock:
            if generation == self._cache_generation:
                self._cache[key] = value
        return value

    def set(self, key: str, value: str) -> None:
        self.set_many({key: value})

    def set_many(self, values: dict[str, str]) -> None:
        """Save one settings form in one transaction, then expose its values to the UI."""
        if not values:
            return
        with self._cache_lock:
            generation = self._cache_generation
        saved = False
        try:
            updated_at = self._now().isoformat()
            connection = sqlite3.connect(self._database_path)
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS central_setting_versions ("
                    "setting_key TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
                )
                # Compare before replacing values, under the first UPSERT's writer lock.
                # A missing version still registers an unchanged value for its first sync.
                connection.executemany(
                    "INSERT INTO central_setting_versions(setting_key,updated_at) VALUES(?,?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET updated_at=excluded.updated_at "
                    "WHERE (SELECT value FROM settings WHERE key=excluded.setting_key) IS NOT ?",
                    tuple((key, updated_at, value) for key, value in values.items()),
                )
                connection.executemany(
                    "INSERT INTO settings(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value "
                    "WHERE settings.value IS NOT excluded.value",
                    tuple(values.items()),
                )
                connection.commit()
            finally:
                connection.close()
            saved = True
        finally:
            with self._cache_lock:
                publish = saved and generation == self._cache_generation
                self._cache_generation += 1
                if publish:
                    self._cache.update(values)
                else:
                    # A newer write/clear may have completed before this writer's ACK.
                    for key in values:
                        self._cache.pop(key, None)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._cache_generation += 1
