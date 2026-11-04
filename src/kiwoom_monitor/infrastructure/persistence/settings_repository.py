from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock
from typing import Callable
from kiwoom_monitor.domain.time_basis import kst_now, as_kst


class SettingsNativeWriteError(ValueError):
    def __init__(self, message: str, *, native_committed: bool | None = False,
                 cache_invalidated: bool | None = None,
                 cache_invalidation_error: Exception | None = None,
                 close_error: Exception | None = None) -> None:
        super().__init__(message)
        # False: no COMMIT/confirmed rollback; None: native result unknown;
        # True: COMMIT acknowledged, with later cleanup/publication failure.
        self.native_committed = native_committed
        self.cache_invalidated = cache_invalidated
        self.cache_invalidation_error = cache_invalidation_error
        self.close_error = close_error


class SettingsRepository:
    def __init__(self, database_path: Path, *, now_provider=None, strict_text: bool = False) -> None:
        self._database_path = database_path
        self.set_now_provider(now_provider or kst_now)
        self._strict_text = strict_text
        self._cache: dict[str, str] = {}
        self._cache_lock = Lock()
        self._cache_generation = 0

    def set_now_provider(self, now_provider) -> None:
        self._now = lambda: as_kst(now_provider())

    @property
    def database_path(self) -> Path:
        return self._database_path

    def get(self, key: str, default: str | None = None) -> str:
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
            if default is not None:
                return default
            raise KeyError(key)
        if self._strict_text and type(row[0]) is not str:
            raise ValueError(f"조회 설정 '{key}'의 원자료 형식이 text가 아닙니다: {type(row[0]).__name__}")
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
                self.write_many_in_transaction(connection, values, updated_at=updated_at)
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

    def write_many_in_transaction(self, connection, values: dict[str, str], *, updated_at: str) -> None:
        """Write using the caller's native connection; caller commits/publishes.

        This does not commit, close, or publish this repository's cached values.
        """
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

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._cache_generation += 1

    def require_native_connection(self, connection: sqlite3.Connection, database_path: Path) -> None:
        """A composite writer must target the actual database of this UI port."""
        files = {name: filename for _, name, filename in connection.execute("PRAGMA database_list")}
        if (not files.get("main") or Path(files["main"]).resolve() != database_path.resolve()
                or self.database_path.resolve() != database_path.resolve()):
            raise SettingsNativeWriteError("테마와 설정 포트의 실제 저장 DB가 일치하지 않습니다.")

    def commit_native_write(self, database_path: Path,
                            write: Callable[[sqlite3.Connection], None]) -> None:
        """Commit one composite producer, then invalidate this same UI port.

        Business SQL stays with the producer. No cache lock spans native I/O.
        A changed __exit__ exception cannot confirm COMMIT or rollback; close
        and cache publication errors retain that phase independently.
        """
        connection = None
        native_committed: bool | None = False
        body_error = failure = close_error = cache_error = None
        cache_invalidated = None
        try:
            if self.database_path.resolve() != database_path.resolve():
                raise SettingsNativeWriteError("테마와 설정은 같은 DB를 사용해야 합니다.")
            connection = sqlite3.connect(database_path)
            self.require_native_connection(connection, database_path)
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    write(connection)
                except Exception as error:
                    body_error = error
                    raise
                native_committed = None
            native_committed = True
        except Exception as error:
            failure = error
            if body_error is not None and error is not body_error:
                native_committed = None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception as error:
                    close_error = error
                    if failure is None:
                        failure = error
        if native_committed is not False:
            try:
                self.clear_cache()
                cache_invalidated = True
            except Exception as error:
                cache_error = error
                cache_invalidated = False
                if failure is None:
                    failure = error
        if failure is None:
            return
        if native_committed is False:
            message = f"테마 변경을 적용하지 못했습니다. COMMIT 전 변경은 되돌렸습니다.\n{failure}"
        elif native_committed is None:
            message = "테마 변경 COMMIT 결과를 확인할 수 없습니다. 현재 테마는 적용 미확인입니다. 재시도 전 DB를 확인하세요."
        else:
            message = "테마 변경은 DB에 저장됐지만 정리 또는 현재 화면 적용에 실패했습니다. 다시 열어 확인하세요."
        if cache_invalidated is False:
            message += " 현재 설정 캐시도 다시 읽을 준비를 마치지 못했습니다."
        raise SettingsNativeWriteError(message, native_committed=native_committed,
            cache_invalidated=cache_invalidated, cache_invalidation_error=cache_error,
            close_error=close_error) from failure
