"""뉴스·매매일지 같은 보조 프로세스와 교환하는 공통 기능."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from functools import lru_cache
from collections.abc import Callable
from typing import Mapping, Sequence


class AuxiliaryProcessManager:
    """하나의 보조 창 프로세스 참조와 시작·종료 상태를 관리한다."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(
        self,
        command: Sequence[str],
        working_directory: Path,
        *,
        below_normal_priority: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.Popen[bytes]:
        environment_options = {} if env is None else {"env": env}
        self._process = (
            launch_auxiliary_process(
                command, working_directory, below_normal_priority=True,
                **environment_options,
            )
            if below_normal_priority
            else launch_auxiliary_process(command, working_directory, **environment_options)
        )
        return self._process

    def stop(
        self,
        *,
        request_shutdown: Callable[[], None] | None = None,
        graceful_timeout: float = 2.0,
        terminate_timeout: float = 1.0,
        kill_timeout: float | None = None,
    ) -> None:
        stop_auxiliary_process(
            self._process,
            request_shutdown=request_shutdown,
            graceful_timeout=graceful_timeout,
            terminate_timeout=terminate_timeout,
            kill_timeout=kill_timeout,
        )
        self._process = None

    def clear(self) -> None:
        self._process = None


class JsonCommandChannel:
    """보조 창 명령의 요청 번호와 원자적 JSON 기록을 함께 관리한다."""

    def __init__(self, path: Path | None, *, time_based_ids: bool = False) -> None:
        self._path = path
        self._time_based_ids = time_based_ids
        self._request_id = 0

    @property
    def request_id(self) -> int:
        return self._request_id

    def advance(self) -> int:
        if self._time_based_ids:
            self._request_id = max(self._request_id + 1, time.time_ns())
        else:
            self._request_id += 1
        return self._request_id

    def send(self, document: Mapping[str, object]) -> int:
        request_id = self.advance()
        if self._path is not None:
            write_json_command(self._path, {**document, "request_id": request_id})
        return request_id


class JsonRequestInbox:
    """다른 보조 창이 기록한 JSON 요청 중 아직 처리하지 않은 것만 읽는다."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._last_request_id = -1

    @property
    def last_request_id(self) -> int:
        return self._last_request_id

    def read_new(self) -> dict[str, object] | None:
        if self._path is None or not self._path.is_file():
            return None
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                return None
            request_id = int(document.get("request_id", -1))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if request_id <= self._last_request_id:
            return None
        self._last_request_id = request_id
        return document


def process_is_alive(process_id: int) -> bool:
    """PID가 현재 실행 중인지 플랫폼에 맞게 확인한다."""
    if process_id <= 0:
        return False
    if sys.platform == "win32":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


def process_start_token(process_id: int) -> str:
    """PID 재사용을 구분할 수 있는 운영체제 시작 시각 토큰을 반환한다."""
    if process_id <= 0:
        return ""
    if sys.platform == "win32":
        class FileTime(ctypes.Structure):
            _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
        if not handle:
            return ""
        try:
            created, exited, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
            if not ctypes.windll.kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user),
            ):
                return ""
            return str((created.high << 32) | created.low)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    if sys.platform == "darwin":
        try:
            return _darwin_process_start_token(process_id)
        except OSError:
            return ""
    try:
        # Linux /proc의 22번째 필드는 부팅 이후 프로세스 시작 tick이다.
        return Path(f"/proc/{process_id}/stat").read_text(encoding="ascii").split()[21]
    except (OSError, IndexError):
        return ""


@lru_cache(maxsize=1)
def _darwin_process_api():
    class BSDInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint32) for name in (
            'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid',
            'ruid', 'rgid', 'svuid', 'svgid', 'reserved',
        )] + [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)] + [
            (name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid')
        ] + [('nice', ctypes.c_int32), ('start_seconds', ctypes.c_uint64),
             ('start_microseconds', ctypes.c_uint64)]

    try:
        query = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True).proc_pidinfo
    except AttributeError:
        raise OSError('PROC_PIDTBSDINFO API unavailable') from None
    query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    query.restype = ctypes.c_int
    return BSDInfo, query


def _darwin_process_start_token(process_id: int) -> str:
    BSDInfo, query = _darwin_process_api()
    info = BSDInfo()
    ctypes.set_errno(0)
    # PROC_PIDTBSDINFO returns its full byte count; errno distinguishes ESRCH
    # from denied/unavailable inspection. A short response has no proven identity.
    expected = ctypes.sizeof(info)
    received = query(process_id, 3, 0, ctypes.byref(info), expected)
    if received == 0:
        raise OSError(ctypes.get_errno(), 'PROC_PIDTBSDINFO failed')
    if received != expected:
        raise OSError('PROC_PIDTBSDINFO returned an incomplete identity')
    if info.pid != process_id or not info.start_seconds or info.start_microseconds >= 1_000_000:
        raise OSError('PROC_PIDTBSDINFO returned an invalid identity')
    return f'darwin:{info.start_seconds}:{info.start_microseconds:06d}'


def process_identity_document(process_id: int | None = None) -> dict[str, object]:
    pid = os.getpid() if process_id is None else int(process_id)
    return {"pid": pid, "start_token": process_start_token(pid)}


def read_process_identity(path: Path) -> tuple[int, str]:
    """새 JSON 상태를 읽고, 과거 PID 한 줄 파일은 검증 불가 토큰으로 반환한다."""
    try:
        raw = path.read_text(encoding="ascii").strip()
        value = json.loads(raw)
        if isinstance(value, dict):
            return int(value.get("pid", 0)), str(value.get("start_token", ""))
        return int(value), ""
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 0, ""


def process_identity_is_alive(process_id: int, start_token: str) -> bool:
    if sys.platform == 'darwin':
        return process_identity_state(process_id, start_token) == 'running'
    return bool(
        process_id > 0
        and start_token
        and process_is_alive(process_id)
        and process_start_token(process_id) == start_token
    )


def process_identity_state(process_id: int, start_token: str) -> str:
    """Return running, exited, or unknown without mistaking access denial for exit."""
    if type(process_id) is not int or process_id <= 0 or not start_token:
        return 'unknown'
    if sys.platform == 'darwin':
        try:
            current = _darwin_process_start_token(process_id)
        except OSError as error:
            return 'exited' if error.errno == errno.ESRCH else 'unknown'
        return 'running' if current == start_token else 'exited'
    current = process_start_token(process_id)
    if current:
        if current != start_token:
            return 'exited'
        return 'running' if process_is_alive(process_id) else 'exited'
    if sys.platform == 'win32':
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return 'unknown'
        return 'exited' if ctypes.windll.kernel32.GetLastError() == 87 else 'unknown'
    return 'exited' if not Path(f'/proc/{process_id}').exists() else 'unknown'


def write_json_command(path: Path, document: Mapping[str, object]) -> None:
    """보조 창이 불완전한 명령 파일을 읽지 않도록 임시 파일을 원자적으로 교체한다."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(document), ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def build_auxiliary_command(
    module_name: str,
    frozen_switch: str,
    arguments: Sequence[str],
    *,
    frozen: bool | None = None,
    executable: str | None = None,
    prefix: str | None = None,
) -> list[str]:
    """개발 실행과 설치본 실행에 맞는 보조 창 명령을 만든다."""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    python_executable = executable or sys.executable
    if is_frozen:
        command = [python_executable, frozen_switch]
    else:
        environment_prefix = Path(prefix or sys.prefix)
        venv_python = environment_prefix / "Scripts" / "python.exe"
        command = [
            str(venv_python if venv_python.is_file() else python_executable),
            "-m",
            module_name,
        ]
    command.extend(str(argument) for argument in arguments)
    return command


def launch_auxiliary_process(
    command: Sequence[str],
    working_directory: Path,
    *,
    below_normal_priority: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Windows에서 명령창을 띄우지 않고 보조 프로세스를 시작한다."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if below_normal_priority:
        creationflags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    environment_options = {} if env is None else {"env": env}
    return subprocess.Popen(
        list(command),
        cwd=str(working_directory),
        creationflags=creationflags,
        **environment_options,
    )


def stop_auxiliary_process(
    process: subprocess.Popen[bytes] | None,
    *,
    request_shutdown: Callable[[], None] | None = None,
    graceful_timeout: float = 2.0,
    terminate_timeout: float = 1.0,
    kill_timeout: float | None = None,
) -> None:
    """정상 종료부터 강제종료 순서로 요청하고 실제 종료까지 확인한다."""
    if process is None or process.poll() is not None:
        return
    if request_shutdown is not None:
        request_shutdown()
    try:
        process.wait(timeout=graceful_timeout)
        return
    except subprocess.TimeoutExpired:
        process.terminate()
    try:
        process.wait(timeout=terminate_timeout)
        return
    except subprocess.TimeoutExpired:
        process.kill()
    process.wait(timeout=kill_timeout)
