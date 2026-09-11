from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from .app import create_app
from .config import CentralServerSettings


def main() -> None:
    _ensure_standard_streams()
    try:
        import uvicorn
    except ImportError as error:
        raise SystemExit("중앙 서버 의존성을 설치하세요: pip install -e .[server]") from error
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--parent-pid", type=int, default=0)
    arguments, _ = parser.parse_known_args()
    settings = CentralServerSettings.from_environment()
    server = uvicorn.Server(uvicorn.Config(
        create_app(settings), host=settings.host, port=settings.port, log_level="info",
    ))
    if arguments.parent_pid > 0:
        threading.Thread(
            target=_stop_with_parent, args=(server, arguments.parent_pid), daemon=True,
            name="central-server-parent-watch",
        ).start()
    server.run()


def _ensure_standard_streams() -> None:
    """Provide streams required by Uvicorn in a PyInstaller windowed child."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _stop_with_parent(server: object, parent_pid: int) -> None:
    while not getattr(server, "should_exit", False):
        if not _process_is_alive(parent_pid):
            setattr(server, "should_exit", True)
            return
        time.sleep(1)


def _process_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(process_id, 0)
            return True
        except OSError:
            return False
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, process_id)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main()
