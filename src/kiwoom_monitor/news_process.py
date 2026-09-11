from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.infrastructure.logging_config import configure_logging
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.persistence.news_database import initialize_news_database
from kiwoom_monitor.presentation.stock_news_window import StockNewsWindow


def _application_icon_path() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return bundle_root / "resources" / "app_icon.png"
    return Path(__file__).resolve().parents[2] / "resources" / "app_icon.png"


def _set_taskbar_app_id() -> None:
    """별도 프로세스인 뉴스창을 메인 앱과 같은 작업표시줄 그룹으로 묶는다."""
    if sys.platform != "win32":
        return
    app_id = "Kuni.KiwoomRealtimeMonitor" if getattr(sys, "frozen", False) else "Kuni.KiwoomRealtimeMonitor.Test"
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass


def _find_visible_window(process_id: int) -> int:
    """지정한 프로세스의 보이는 최상위 메인 창 핸들을 찾는다."""
    if sys.platform != "win32":
        return 0
    candidates: list[tuple[bool, int]] = []
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(hwnd: int, _parameter: int) -> bool:
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value != process_id or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        candidates.append(("키움 실시간 모니터" in buffer.value, int(hwnd)))
        return True

    user32.EnumWindows(collect, 0)
    if not candidates:
        return 0
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _raise_hwnd_without_focus(hwnd: int) -> None:
    if sys.platform != "win32" or not hwnd:
        return
    set_window_pos = ctypes.windll.user32.SetWindowPos
    set_window_pos.argtypes = (
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    )
    set_window_pos.restype = wintypes.BOOL
    flags = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE|NOMOVE|NOACTIVATE|NOOWNERZORDER
    # TOPMOST→NOTOPMOST 전환은 다른 프로그램까지 전체 Z 순서를 다시
    # 계산하게 해 Chrome·Codex 창이 순간적으로 사라졌다 나타날 수 있다.
    # 뉴스창 한 개만 일반 최상단으로 이동하고 키보드 포커스는 유지한다.
    set_window_pos(hwnd, wintypes.HWND(0), 0, 0, 0, 0, flags)  # HWND_TOP


def _parent_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if sys.platform == "win32":
        # Windows의 os.kill(pid, 0)은 POSIX식 생존 확인으로 동작하지 않아
        # 실행 중인 pythonw.exe에도 OSError를 반환할 수 있다. 프로세스 핸들을
        # 열고 STILL_ACTIVE 상태를 확인해야 한다.
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, process_id)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


def _file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _news_content_signature(news_database_path: Path) -> tuple[int, int]:
    """뉴스 프로세스가 직접 소유하는 로컬 콘텐츠 변경만 감지한다.

    실시간 순위·분봉도 쓰는 ``monitor.sqlite3``의 파일 수정 시각은 테마
    변경 근거가 아니다. 테마 변경은 메인 프로세스의 전용 dispatcher가
    중앙에 반영하므로 여기서는 뉴스/AI DB만 업로드 계기로 사용한다.
    """
    return _file_signature(news_database_path)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--command-file", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    options = parser.parse_args(arguments)
    config_path = Path(options.config)
    database_path = Path(options.database)
    command_path = Path(options.command_file)
    state_path = command_path.with_name("news_window_state.json")
    configure_logging(database_path.parent / "logs")
    initialize_news_database(database_path)

    _set_taskbar_app_id()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("키움 실시간 모니터 뉴스")
    app.setWindowIcon(QIcon(str(_application_icon_path())))
    # 메인 앱이 명령 파일을 쓰기 전에는 표시된 창이 없다. 이때 Qt의 기본
    # lastWindowClosed 동작으로 자식 프로세스가 먼저 끝나지 않게 유지한다.
    # 사용자가 뉴스창을 닫아도 프로세스는 대기하고 다음 더블클릭에 재사용한다.
    app.setQuitOnLastWindowClosed(False)
    window = StockNewsWindow(config_path, database_path)
    window.setWindowIcon(app.windowIcon())
    logger = logging.getLogger(__name__)
    logger.info("뉴스 전용 프로세스 시작: parent=%s", options.parent_pid)
    last_request_id = -1
    restore_after_main = False
    last_published_visibility: bool | None = None

    def publish_visibility(*, force: bool = False) -> None:
        nonlocal last_published_visibility
        visible = window.isVisible() and not window.isMinimized()
        if not force and visible == last_published_visibility:
            return
        temporary = state_path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps({"visible": visible, "pid": os.getpid(), "updated_at": time.time_ns()}),
                encoding="utf-8",
            )
            temporary.replace(state_path)
            last_published_visibility = visible
        except OSError:
            logger.debug("뉴스창 상태 파일 저장 실패", exc_info=True)

    def raise_without_focus() -> None:
        """Windows 포커스는 메인창에 둔 채 뉴스창만 같은 Z 순서로 올린다."""
        if sys.platform != "win32":
            window.raise_()
            return
        # 단순 raise()는 백그라운드 프로세스 포커스 보호에 막힐 수 있다.
        # TOPMOST→NOTOPMOST를 연속 적용하면 포커스와 항상-위 속성은 건드리지
        # 않으면서 현재 앱들 위의 일반 창 순서로 이동한다.
        _raise_hwnd_without_focus(int(window.winId()))

    def dock_beside_main(geometry: object, mode: str) -> None:
        if not isinstance(geometry, list) or len(geometry) != 4:
            return
        try:
            main_x, main_y, main_width, main_height = (int(value) for value in geometry)
        except (TypeError, ValueError):
            return
        screen = QApplication.screenAt(QPoint(main_x + main_width // 2, main_y + main_height // 2))
        available = screen.availableGeometry() if screen is not None else QApplication.primaryScreen().availableGeometry()
        news_frame = window.frameGeometry()
        news_width = news_frame.width()
        news_height = news_frame.height()
        if mode == "docked_left":
            x, y = main_x - news_width, main_y
        elif mode == "docked_top":
            x, y = main_x, main_y - news_height
        elif mode == "docked_bottom":
            x, y = main_x, main_y + main_height
        else:
            x, y = main_x + main_width, main_y
        x = max(available.left(), min(x, available.right() - news_width + 1))
        y = max(available.top(), min(main_y, available.bottom() - news_height + 1))
        if mode in {"docked_top", "docked_bottom"}:
            y = main_y - news_height if mode == "docked_top" else main_y + main_height
            y = max(available.top(), min(y, available.bottom() - news_height + 1))
        window.move(x, y)

    def poll_command() -> None:
        nonlocal last_request_id, restore_after_main
        try:
            document = json.loads(command_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        request_id = int(document.get("request_id", -1))
        if request_id <= last_request_id:
            return
        last_request_id = request_id
        action = str(document.get("action", "show"))
        mode = str(document.get("window_mode", "independent"))
        if action == "shutdown":
            logger.info("뉴스 전용 프로세스 종료 명령 수신")
            window.shutdown()
            publish_visibility(force=True)
            app.quit()
            return
        if action == "minimize":
            restore_after_main = window.isVisible() and not window.isMinimized()
            if restore_after_main:
                window.showMinimized()
            publish_visibility(force=True)
            return
        if action == "restore":
            if restore_after_main:
                window.showNormal()
                # showNormal()만 호출하면 별도 프로세스인 뉴스창이 복원은
                # 되어도 메인창 뒤에 남을 수 있다. 최소화 전에 보이던 창은
                # 포커스를 빼앗지 않는 방식으로 두 창의 Z 순서도 함께 복원한다.
                raise_without_focus()
                restore_after_main = False
            publish_visibility(force=True)
            return
        if action == "reload_settings":
            window._on_settings_saved()
            publish_visibility(force=True)
            return
        if action == "sync":
            if mode.startswith("docked_") or mode == "docked":
                dock_beside_main(document.get("main_geometry"), "docked_right" if mode == "docked" else mode)
            if (mode == "linked" or mode.startswith("docked_") or mode == "docked") and window.isVisible():
                raise_without_focus()
            return
        code, name = str(document.get("code", "")), str(document.get("name", "")).strip()
        if code and name:
            window.set_stock(
                code, name, activate=bool(document.get("activate", True)),
                journal_group_id=str(document.get("journal_group_id", "")),
                trade_date=str(document.get("trade_date", "")),
            )
            if mode.startswith("docked_") or mode == "docked":
                dock_beside_main(document.get("main_geometry"), "docked_right" if mode == "docked" else mode)
            publish_visibility(force=True)

    command_timer = QTimer()
    command_timer.setInterval(80)
    command_timer.timeout.connect(poll_command)
    command_timer.start()
    parent_timer = QTimer()
    parent_timer.setInterval(1_000)

    def stop_if_parent_exited() -> None:
        if _parent_is_alive(options.parent_pid):
            return
        logger.info("메인 프로세스 종료 감지: parent=%s", options.parent_pid)
        window.shutdown()
        app.quit()

    parent_timer.timeout.connect(stop_if_parent_exited)
    parent_timer.start()
    news_was_active = False
    activation_timer = QTimer()
    activation_timer.setInterval(100)

    def sync_parent_on_news_activation() -> None:
        nonlocal news_was_active
        active = window.isVisible() and window.isActiveWindow()
        if active and not news_was_active:
            mode = str(window._window_mode.currentData() or "independent")
            if mode == "linked" or mode.startswith("docked_") or mode == "docked":
                # 부모를 먼저 올리고 뉴스창을 다시 위에 두어 사용자가 선택한
                # 뉴스창의 키보드 포커스와 시각적 우선순위를 유지한다.
                _raise_hwnd_without_focus(_find_visible_window(options.parent_pid))
                raise_without_focus()
        news_was_active = active
        publish_visibility()

    activation_timer.timeout.connect(sync_parent_on_news_activation)
    activation_timer.start()

    # 뉴스 수집과 AI 분석은 뉴스 전용 프로세스의 로컬 DB에 먼저 안전하게
    # 저장된다. 중앙 모드에서는 파일 변경을 감지한 뒤 별도 daemon 스레드가
    # 중앙 저장소에 복사한다. 네트워크 지연이나 서버 장애가 뉴스창 Qt 루프는
    # 물론 메인 실시간 표에도 전달되지 않는다.
    content_sync_timer: QTimer | None = None
    content_sync_running = threading.Event()
    last_content_signature: tuple[int, int] | None = None
    try:
        source = DataSourceConfig(config_path.with_name("data_source.json")).load()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logger.warning("중앙 콘텐츠 설정을 읽지 못했습니다: %s", error)
        source = None
    if source is not None and source.mode in {"local_server", "personal_server"}:
        content_sync = CentralContentSyncService(CentralContentClient(
            source.server_url, source.access_token,
        ))
        main_database_path = database_path.with_name("monitor.sqlite3")
        last_content_signature = _news_content_signature(database_path)

        def schedule_content_sync() -> None:
            nonlocal last_content_signature
            signature = _news_content_signature(database_path)
            if content_sync_running.is_set():
                return
            local_changed = signature != last_content_signature
            content_sync_running.set()

            def run() -> None:
                nonlocal last_content_signature
                try:
                    if local_changed:
                        content_sync.push(main_database_path, database_path)
                    pulled = content_sync.pull(main_database_path, database_path)
                    last_content_signature = _news_content_signature(database_path)
                    logger.info(
                        "중앙 콘텐츠 동기화 완료: 내려받기 %s건, 로컬 변경=%s",
                        pulled.total, local_changed,
                    )
                except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
                    logger.warning("중앙 콘텐츠 변경분 보존 실패(로컬 자료 유지): %s", error)
                finally:
                    content_sync_running.clear()

            threading.Thread(target=run, name="news-central-content-sync", daemon=True).start()

        content_sync_timer = QTimer()
        content_sync_timer.setInterval(60_000)
        content_sync_timer.timeout.connect(schedule_content_sync)
        content_sync_timer.start()
        QTimer.singleShot(1_000, schedule_content_sync)
    publish_visibility(force=True)
    poll_command()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
