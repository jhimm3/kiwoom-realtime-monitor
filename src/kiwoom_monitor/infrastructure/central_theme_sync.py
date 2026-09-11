from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService


logger = logging.getLogger(__name__)


class CentralThemeSyncDispatcher:
    """테마 저장 직후 중앙 스냅샷 교체를 UI 밖에서 합쳐 실행한다."""

    def __init__(self, service: CentralContentSyncService, database_path: Path,
                 *, debounce_seconds: float = 0.4,
                 retry_delays: tuple[float, ...] = (2.0, 10.0, 30.0, 60.0),
                 pending_path: Path | None = None) -> None:
        self._service = service
        self._database_path = database_path
        self._debounce_seconds = max(0.05, float(debounce_seconds))
        self._retry_delays = tuple(max(0.05, float(value)) for value in retry_delays) or (60.0,)
        self._pending_path = pending_path or database_path.with_name(".central_theme_sync_pending")
        self._state_lock = threading.Lock()
        self._execution_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._closed = False
        self._generation = 1 if self._pending_path.is_file() else 0
        self._retry_index = 0

    def notify(self) -> None:
        """연속 일괄 수정을 한 번의 중앙 전송으로 합친다."""
        with self._state_lock:
            if self._closed:
                return
            self._generation += 1
            self._retry_index = 0
            self._write_pending(f"{time.time():.9f}")
            if self._timer is not None:
                self._timer.cancel()
            self._schedule_locked(self._debounce_seconds)

    @property
    def has_pending(self) -> bool:
        return self._pending_path.is_file()

    def flush_pending(self) -> bool:
        """이전 실행에서 남은 변경과 중앙 완료본 중 최신 자료를 보존한다."""
        if not self.has_pending:
            return True
        return self._run()

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()

    def _run(self) -> bool:
        with self._state_lock:
            if self._closed:
                return False
            self._timer = None
            generation = self._generation
            pending_token = self._read_pending()
        try:
            with self._execution_lock:
                remote_snapshot, remote_completed_at = self._service.load_theme_snapshot()
                local_changed_at = self._pending_time(pending_token)
                if remote_completed_at is not None and remote_completed_at > local_changed_at:
                    self._service.apply_theme_snapshot(self._database_path, remote_snapshot)
                    logger.info(
                        "NAS 테마가 로컬 대기 변경보다 최신이어서 NAS 스냅샷을 적용했습니다."
                    )
                else:
                    self._service.replace_themes(self._database_path)
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
            # 중앙 장애가 테마 편집이나 메인 실시간 화면을 막지 않는다.
            logger.warning("테마 중앙 즉시 보존 실패(로컬 자료 유지): %s", error)
            with self._state_lock:
                if not self._closed and generation == self._generation and self._timer is None:
                    delay = self._retry_delays[min(self._retry_index, len(self._retry_delays) - 1)]
                    self._retry_index += 1
                    self._schedule_locked(delay)
            return False
        with self._state_lock:
            if generation == self._generation:
                self._retry_index = 0
                self._clear_pending(pending_token)
        return True

    def _schedule_locked(self, delay: float) -> None:
        self._timer = threading.Timer(delay, self._run)
        self._timer.daemon = True
        self._timer.name = "central-theme-sync"
        self._timer.start()

    def _write_pending(self, token: str) -> None:
        try:
            self._pending_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._pending_path.with_suffix(self._pending_path.suffix + ".tmp")
            temporary.write_text(token, encoding="utf-8")
            temporary.replace(self._pending_path)
        except OSError as error:
            logger.warning("테마 중앙 동기화 대기 상태 저장 실패: %s", error)

    def _read_pending(self) -> str:
        try:
            return self._pending_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _pending_time(token: str) -> float:
        try:
            value = float(token.strip())
        except ValueError:
            return 0.0
        # 이전 버전은 time_ns 정수를 기록했다. 남아 있는 표식도 안전하게 비교한다.
        return value / 1_000_000_000.0 if value > 10_000_000_000.0 else value

    def _clear_pending(self, expected: str) -> None:
        try:
            if not self._pending_path.exists() or self._read_pending() != expected:
                return
            self._pending_path.unlink()
        except OSError as error:
            logger.warning("테마 중앙 동기화 대기 상태 정리 실패: %s", error)
