"""앱 종료 후 부분 업데이트를 적용하는 독립 실행 도우미.

PyInstaller one-file EXE로 묶어 앱 데이터 폴더에 임시 복사 후 실행한다.
PowerShell이나 앱 설치 폴더의 Python 환경에 의존하지 않는다.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from tkinter import Tk, ttk, messagebox


class UpdateError(RuntimeError):
    """안전하게 적용할 수 없는 업데이트 패키지다."""


class UpdateProgress:
    def __init__(self) -> None:
        self._window = Tk()
        self._window.title("키움 실시간 모니터 업데이트")
        self._window.geometry("420x145")
        self._window.resizable(False, False)
        self._window.protocol("WM_DELETE_WINDOW", lambda: None)
        frame = ttk.Frame(self._window, padding=22)
        frame.pack(fill="both", expand=True)
        self._label = ttk.Label(frame, text="업데이트를 준비하고 있습니다…")
        self._label.pack(anchor="w")
        self._bar = ttk.Progressbar(frame, orient="horizontal", mode="determinate", maximum=100)
        self._bar.pack(fill="x", pady=(14, 0))
        self._window.update()

    def show(self, message: str, value: int | None = None) -> None:
        self._label.configure(text=message)
        if value is not None:
            self._bar.configure(value=max(0, min(100, value)))
        self._window.update_idletasks()
        self._window.update()

    def close(self) -> None:
        self._window.destroy()


def _write_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def _safe_relative(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise UpdateError("업데이트 파일 목록이 올바르지 않습니다.")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or any(part in {"", "."} or ":" in part for part in relative.parts)
    ):
        raise UpdateError("허용되지 않은 업데이트 경로가 포함되어 있습니다.")
    return Path(*relative.parts)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _wait_for_process(process_id: int, progress: UpdateProgress) -> None:
    progress.show("앱 종료를 기다리고 있습니다…", 0)
    if os.name == "nt":
        synchronize = 0x00100000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, process_id)
        if handle:
            try:
                while ctypes.windll.kernel32.WaitForSingleObject(handle, 100) == wait_timeout:
                    progress.show("앱 종료를 기다리고 있습니다…", 0)
                return
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    while True:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            raise UpdateError("앱 종료 상태를 확인할 수 없습니다.")
        time.sleep(0.1)
        progress.show("앱 종료를 기다리고 있습니다…", 0)


def _extract_archive(archive_path: Path, staging: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for item in archive.infolist():
                destination = staging / Path(*PurePosixPath(item.filename).parts)
                if not _inside(destination, staging):
                    raise UpdateError("업데이트 압축 파일에 허용되지 않은 경로가 있습니다.")
            archive.extractall(staging)
    except (OSError, zipfile.BadZipFile) as error:
        raise UpdateError("업데이트 압축 파일을 열 수 없습니다.") from error


def _read_manifest(staging: Path) -> tuple[list[Path], list[Path]]:
    manifest_path = staging / "update_manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise UpdateError("업데이트 목록을 읽을 수 없습니다.") from error
    if not isinstance(document, dict):
        raise UpdateError("업데이트 목록 형식이 올바르지 않습니다.")
    changed, deleted = document.get("changed", []), document.get("deleted", [])
    if not isinstance(changed, list) or not isinstance(deleted, list):
        raise UpdateError("업데이트 목록 형식이 올바르지 않습니다.")
    changed_paths = [_safe_relative(item) for item in changed]
    deleted_paths = [_safe_relative(item) for item in deleted]
    for relative in changed_paths:
        source = staging / relative
        if not _inside(source, staging) or not source.is_file():
            raise UpdateError("교체할 업데이트 파일을 찾을 수 없습니다.")
    return changed_paths, deleted_paths


def apply_archives(archive_paths: tuple[Path, ...], target_root: Path, progress: UpdateProgress | None = None, log_path: Path | None = None) -> None:
    """검증된 ZIP 여러 개를 오래된 버전부터 차례로 적용한다.

    화면 없이 호출할 수 있어 임시 폴더 테스트에서도 실제 파일 교체 로직을 검증한다.
    """
    if not archive_paths or not target_root.is_dir():
        raise UpdateError("업데이트 대상 경로를 확인할 수 없습니다.")
    for index, archive_path in enumerate(archive_paths, start=1):
        if not archive_path.is_file():
            raise UpdateError("업데이트 파일을 찾을 수 없습니다.")
        staging = archive_path.parent / f"staging-{os.getpid()}-{index}"
        try:
            if progress is not None:
                progress.show(f"업데이트 {index}/{len(archive_paths)} 파일을 준비하고 있습니다…", int(5 + 90 * (index - 1) / len(archive_paths)))
            _extract_archive(archive_path, staging)
            changed, deleted = _read_manifest(staging)
            total_size = sum((staging / item).stat().st_size for item in changed)
            copied_size = 0
            for relative in changed:
                source, destination = staging / relative, target_root / relative
                if not _inside(destination, target_root):
                    raise UpdateError("허용되지 않은 교체 대상 경로입니다.")
                if progress is not None:
                    within_archive = copied_size / max(1, total_size)
                    progress.show(f"업데이트 {index}/{len(archive_paths)} 파일을 교체하고 있습니다…", int(5 + 90 * ((index - 1 + within_archive) / len(archive_paths))))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied_size += source.stat().st_size
                if log_path is not None:
                    _write_log(log_path, f"{index}/{len(archive_paths)} 교체 완료: {relative.as_posix()}")
            for relative in deleted:
                destination = target_root / relative
                if not _inside(destination, target_root):
                    raise UpdateError("허용되지 않은 삭제 대상 경로입니다.")
                destination.unlink(missing_ok=True)
                if log_path is not None:
                    _write_log(log_path, f"{index}/{len(archive_paths)} 삭제 처리: {relative.as_posix()}")
            archive_path.unlink(missing_ok=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def apply_update(wait_pid: int, archive_paths: tuple[Path, ...], target_root: Path, executable: Path, log_path: Path) -> None:
    progress = UpdateProgress()
    try:
        _write_log(log_path, f"업데이트 도우미 시작 ({len(archive_paths)}개)")
        if not _inside(executable, target_root):
            raise UpdateError("업데이트 대상 경로를 확인할 수 없습니다.")
        _wait_for_process(wait_pid, progress)
        _write_log(log_path, "앱 종료 확인")
        apply_archives(archive_paths, target_root, progress, log_path)
        progress.show("업데이트를 마무리하고 있습니다…", 100)
        _write_log(log_path, "업데이트 완료, 앱 재실행")
        subprocess.Popen([str(executable)], cwd=str(target_root))
        time.sleep(0.4)
        progress.close()
    except Exception as error:
        _write_log(log_path, f"업데이트 실패: {error}")
        progress.show(f"업데이트에 실패했습니다. {error}", 0)
        messagebox.showerror("키움 실시간 모니터 업데이트", f"업데이트에 실패했습니다.\n{error}")
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-pid", required=True, type=int)
    parser.add_argument("--archive", required=True, action="append", type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()
    try:
        apply_update(args.wait_pid, tuple(args.archive), args.target, args.exe, args.log)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
