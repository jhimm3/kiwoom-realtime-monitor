"""메인 UI와 독립적으로 실행되는 동기화·업데이트 작업."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from kiwoom_monitor.infrastructure.app_paths import AppPaths
from kiwoom_monitor.infrastructure.persistence.google_drive_sync import (
    GoogleDriveSyncError,
    GoogleDriveSyncService,
)
from kiwoom_monitor.infrastructure.update_planner import UpdateStep, build_update_plan
from kiwoom_monitor.presentation.app_metadata import APP_VERSION


class GoogleDriveSyncWorker(QThread):
    """Drive 통신만 별도 스레드에서 실행해 표·설정 창을 멈추지 않는다."""

    completed = Signal(str)
    metadata_received = Signal(str)
    failed = Signal(str)

    def __init__(self, service: GoogleDriveSyncService, operation: str, target: str, interactive: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._operation = operation
        self._target = target
        self._interactive = interactive

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            if self._operation == "metadata":
                self.metadata_received.emit(self._service.latest_modified_time(interactive=self._interactive, target=self._target))
                return
            action = self._service.upload if self._operation == "upload" else self._service.download
            self.completed.emit(action(interactive=self._interactive, target=self._target))
        except GoogleDriveSyncError as error:
            self.failed.emit(str(error))


class UpdateCheckWorker(QThread):
    """GitHub Release 확인을 별도 스레드에서 수행한다."""

    completed = Signal(object)
    failed = Signal(str)

    RELEASES_API_URL = "https://api.github.com/repos/jhimm3/kiwoom-realtime-monitor/releases?per_page=100"

    def run(self) -> None:
        try:
            request = Request(self.RELEASES_API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "KiwoomMonitor"})
            with urlopen(request, timeout=10) as response:
                documents = json.loads(response.read().decode("utf-8"))
            if not isinstance(documents, list):
                raise ValueError("릴리즈 목록 형식이 올바르지 않습니다.")
            self.completed.emit(build_update_plan(APP_VERSION, documents))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            self.failed.emit(str(error))


class UpdateDownloadWorker(QThread):
    """여러 개의 부분 업데이트 ZIP을 검증 후 순서대로 내려받는다."""

    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, steps: tuple[UpdateStep, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps = steps

    def run(self) -> None:
        try:
            folder = AppPaths.for_current_user().data_dir.parent / "updates"
            folder.mkdir(parents=True, exist_ok=True)
            planned_size = sum(max(1, step.size) for step in self._steps)
            completed_size = 0
            archives: list[str] = []
            for step in self._steps:
                destination = folder / f"KiwoomMonitor-Update-{step.version}.zip"
                temporary = destination.with_suffix(".zip.part")
                checksum_request = Request(step.checksum_url, headers={"User-Agent": "KiwoomMonitor"})
                with urlopen(checksum_request, timeout=15) as response:
                    checksum_text = response.read(8 * 1024).decode("utf-8", errors="strict")
                match = re.fullmatch(r"\s*([0-9a-fA-F]{64})(?:\s+\*?[^\r\n]+)?\s*", checksum_text)
                if match is None:
                    raise ValueError("업데이트 검증 파일 형식이 올바르지 않습니다.")
                request = Request(step.update_url, headers={"User-Agent": "KiwoomMonitor"})
                digest = hashlib.sha256()
                with urlopen(request, timeout=30) as response, temporary.open("wb") as stream:
                    received = 0
                    while block := response.read(1024 * 1024):
                        if self.isInterruptionRequested():
                            temporary.unlink(missing_ok=True)
                            return
                        stream.write(block)
                        digest.update(block)
                        received += len(block)
                        self.progress.emit(min(100, int((completed_size + min(received, max(1, step.size))) * 100 / planned_size)))
                if digest.hexdigest() != match.group(1).lower():
                    temporary.unlink(missing_ok=True)
                    raise ValueError("업데이트 파일 검증에 실패했습니다. 파일을 교체하지 않았습니다.")
                temporary.replace(destination)
                archives.append(str(destination))
                completed_size += max(1, step.size)
                self.progress.emit(min(100, int(completed_size * 100 / planned_size)))
            self.completed.emit(tuple(archives))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError) as error:
            self.failed.emit(str(error))
