"""Own the separate news window process, command channel, and window sync."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QTimer

from kiwoom_monitor.domain.order_contract import AccountScope
from kiwoom_monitor.presentation.process_control import (
    AuxiliaryProcessManager,
    JsonCommandChannel,
    build_auxiliary_command,
)

logger = logging.getLogger(__name__)


class NewsWindowCoordinator(QObject):
    """The single owner of the news child and its window command state."""

    def __init__(
        self,
        config_path: Path | None,
        database_path: Path | None,
        frame_geometry: Callable[[], object],
        show_status: Callable[[str], None],
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self._database_path = database_path
        self._command_path = database_path.with_name("news_command.json") if database_path else None
        self._state_path = database_path.with_name("news_window_state.json") if database_path else None
        self._process = AuxiliaryProcessManager()
        self._channel = JsonCommandChannel(self._command_path)
        self._frame_geometry = frame_geometry
        self._show_status = show_status
        self._restore_sync_pending = False
        self._dock_timer = QTimer(self)
        self._dock_timer.setSingleShot(True)
        self._dock_timer.setInterval(60)
        self._dock_timer.timeout.connect(self.sync_window)
        # The child polls every 80 ms; do not overwrite restore during that interval.
        self._restore_sync_timer = QTimer(self)
        self._restore_sync_timer.setSingleShot(True)
        self._restore_sync_timer.setInterval(250)
        self._restore_sync_timer.timeout.connect(self._finish_restore_sync)

    @property
    def available(self) -> bool:
        return self._config_path is not None and self._database_path is not None

    @property
    def database_path(self) -> Path | None:
        return self._database_path

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    @property
    def is_running(self) -> bool:
        return self._process.is_running

    @property
    def request_id(self) -> int:
        return self._channel.request_id

    def advance_request(self) -> int:
        return self._channel.advance()

    def is_visible(self) -> bool:
        if self._state_path is None:
            return False
        try:
            document = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(document, dict) and bool(document.get("visible", False))

    @staticmethod
    def window_mode() -> str:
        mode = str(QSettings("KiwoomMonitor", "StockNewsWindow").value("window_mode", "independent"))
        if mode == "docked":
            return "docked_right"
        valid = {"independent", "linked", "docked_right", "docked_left", "docked_top", "docked_bottom"}
        return mode if mode in valid else "independent"

    def ensure_started(self) -> None:
        if self._process.is_running or not self.available or self._command_path is None:
            return
        assert self._config_path is not None and self._database_path is not None
        command = build_auxiliary_command("kiwoom_monitor.news_process", "--news-process", [
            "--config", str(self._config_path),
            "--database", str(self._database_path),
            "--command-file", str(self._command_path),
            "--parent-pid", str(os.getpid()),
        ])
        project_root = self._config_path.parent.parent
        working_directory = project_root if (project_root / "pyproject.toml").is_file() else Path(sys.executable).resolve().parent
        try:
            if self._state_path is not None:
                self._state_path.unlink(missing_ok=True)
            self._process.start(command, working_directory)
        except OSError as error:
            logger.warning("뉴스 프로세스를 시작하지 못했습니다: %s", error)
            self._show_status("뉴스창을 시작하지 못했습니다.")

    def show_market_news(self) -> None:
        self.ensure_started()
        self.send_command(action="market_news")

    def show_stock_news(self, code: str, name: str, *, activate: bool = True) -> None:
        self.ensure_started()
        self.send_command(code, name, activate=activate)

    def send_command(self, code: str = "", name: str = "", *, activate: bool = True,
                     action: str = "show", journal_group_id: str = "", trade_date: str = "",
                     origin_scope: AccountScope | None = None,
                     account_scope: AccountScope | None = None) -> None:
        if self._command_path is None:
            return
        if (origin_scope is None) != (account_scope is None):
            logger.warning("불완전한 계좌 범위가 포함된 뉴스 명령을 거절했습니다.")
            return
        # Use the outer frame, including title bar and borders, for docking.
        frame = self._frame_geometry()
        document = {
            "action": action,
            "code": code,
            "name": name,
            "activate": activate,
            "window_mode": self.window_mode(),
            "main_geometry": [frame.x(), frame.y(), frame.width(), frame.height()],
            "journal_group_id": journal_group_id,
            "trade_date": trade_date,
        }
        if origin_scope is not None and account_scope is not None:
            if (
                origin_scope.broker != account_scope.broker
                or origin_scope.environment != account_scope.environment
            ):
                logger.warning("서로 다른 계좌 환경이 포함된 뉴스 명령을 거절했습니다.")
                return
            document["origin_scope"] = origin_scope.to_dict()
            document["account_scope"] = account_scope.to_dict()
        try:
            self._channel.send(document)
        except OSError as error:
            logger.warning("뉴스 프로세스 명령을 저장하지 못했습니다: %s", error)

    def sync_window(self) -> None:
        if self.is_running and (self.window_mode() == "linked" or self.window_mode().startswith("docked_")):
            self.send_command(action="sync", activate=False)

    def schedule_geometry_sync(self) -> None:
        if self.window_mode().startswith("docked_"):
            self._dock_timer.start()

    def on_window_state_change(self, minimized: bool) -> None:
        if not self.is_running:
            return
        if minimized:
            self._restore_sync_timer.stop()
            self._restore_sync_pending = False
            self.send_command(action="minimize")
        else:
            self._restore_sync_pending = True
            self.send_command(action="restore")
            self._restore_sync_timer.start()

    def on_activation(self) -> None:
        if self.is_running and not self._restore_sync_pending:
            self.sync_window()

    def _finish_restore_sync(self) -> None:
        self._restore_sync_pending = False
        self.sync_window()

    def stop(self) -> None:
        self._dock_timer.stop()
        self._restore_sync_timer.stop()
        self._restore_sync_pending = False
        if self._process.process is not None:
            self._process.stop(
                request_shutdown=lambda: self.send_command(action="shutdown"),
                graceful_timeout=3.0,
                terminate_timeout=1.0,
                kill_timeout=1.0,
            )
