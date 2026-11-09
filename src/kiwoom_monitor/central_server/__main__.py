from __future__ import annotations

import argparse
import os
import sys
import threading
import signal
from dataclasses import replace
from pathlib import Path

from .app import create_app
from .config import CentralServerSettings
from .server_logging import configure_server_logging
from kiwoom_monitor.infrastructure.persistence.strict_restore import StrictRestoreCoordinator
from kiwoom_monitor.infrastructure.owned_server_shutdown import WindowsShutdownEvent


def main() -> None:
    _ensure_standard_streams()
    try:
        import uvicorn
    except ImportError as error:
        raise SystemExit("중앙 서버 의존성을 설치하세요: pip install -e .[server]") from error
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--shutdown-event", default="")
    parser.add_argument("--local-mock-automation", action="store_true")
    arguments, _ = parser.parse_known_args()
    if arguments.shutdown_event and (os.name != "nt" or arguments.parent_pid <= 0):
        parser.error("--shutdown-event requires an owned Windows child")
    settings = CentralServerSettings.from_environment()
    if arguments.local_mock_automation:
        if arguments.parent_pid <= 0 or settings.host != "127.0.0.1" or not settings.database_url.startswith("sqlite:///"):
            parser.error("--local-mock-automation requires an owned loopback server with local SQLite")
        database_path = Path(settings.database_url.removeprefix("sqlite:///"))
        if not database_path.is_absolute():
            parser.error("--local-mock-automation requires an absolute SQLite path")
        settings = replace(settings, local_mock_credential_directory=str(
            database_path.with_name(database_path.name + ".mock-credentials"),
        ))
    configure_server_logging(
        Path(os.environ.get("CENTRAL_SERVER_LOG_DIR", "data/logs")),
        retention_days=_log_retention_days(),
    )
    lease = None
    # The NAS service has no desktop parent and is not part of local restore.
    if arguments.parent_pid > 0 and settings.database_url.startswith("sqlite:///"):
        database_path = Path(settings.database_url.removeprefix("sqlite:///"))
        lease = StrictRestoreCoordinator(
            database_path, database_path.with_name("news.sqlite3"),
        ).enter_child()
    try:
        application = create_app(settings)
        server = uvicorn.Server(uvicorn.Config(
            application, host=settings.host, port=settings.port, log_level="info",
            proxy_headers=False, log_config=None,
            **({"lifespan": "on"} if arguments.parent_pid > 0 else {}),
        ))
        if arguments.parent_pid > 0:
            _run_owned_server(server, application, arguments.parent_pid, arguments.shutdown_event)
        else:
            server.run()
    finally:
        if lease is not None:
            lease.close()


def _log_retention_days() -> int:
    try:
        return max(1, min(int(os.environ.get("CENTRAL_SERVER_LOG_RETENTION_DAYS", "14")), 365))
    except ValueError:
        return 14


def _ensure_standard_streams() -> None:
    """Provide streams required by Uvicorn in a PyInstaller windowed child."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _run_owned_server(server, application, parent_pid: int, event_name: str) -> None:
    event = WindowsShutdownEvent.open(event_name) if event_name else None
    finished = threading.Event()
    errors = []
    watcher = threading.Thread(
        target=_stop_with_parent, args=(server, parent_pid, event, finished, errors),
        daemon=True, name="central-server-parent-watch",
    )
    previous_sigterm = None
    try:
        if os.name != "nt":
            # Uvicorn replays captured SIGTERM after shutdown. Keep that replay
            # inside this owned invocation until its actual drain result is read.
            previous_sigterm = signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))
        watcher.start()
        server.run()
    finally:
        finished.set()
        if watcher.ident is not None:
            watcher.join()  # Its only wait is the existing bounded one-second watch.
        if event is not None:
            event.close()
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
    if errors:
        raise RuntimeError("owned server parent/control watch failed") from errors[0]
    lifespan = getattr(server, "lifespan", None)
    if (not server.started or server.force_exit or lifespan is None
            or lifespan.startup_failed or lifespan.shutdown_failed or lifespan.error_occurred
            or not lifespan.shutdown_event.is_set()
            or getattr(application.state, "shutdown_status", {}).get("state") != "completed"):
        raise RuntimeError("owned server durable shutdown acknowledgement is unconfirmed")


def _stop_with_parent(server: object, parent_pid: int, shutdown_event=None,
                      finished=None, errors=None) -> None:
    finished = finished if finished is not None else threading.Event()
    try:
        while not finished.is_set() and not getattr(server, "should_exit", False):
            if not _process_is_alive(parent_pid):
                setattr(server, "should_exit", True)
                return
            if shutdown_event is not None:
                if shutdown_event.wait(1000):
                    setattr(server, "should_exit", True)
                    return
            else:
                finished.wait(1)
    except BaseException as error:
        setattr(server, "should_exit", True)
        if errors is None:
            raise
        errors.append(error)


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
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x00100000, False, process_id)
    if not handle:
        return False
    try:
        # SYNCHRONIZE permits a zero-time wait, not GetExitCodeProcess. A process
        # can also exit with code 259, so observe its actual signaled state.
        return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main()
