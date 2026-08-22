from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    log_dir: Path
    database_path: Path

    @classmethod
    def for_current_user(cls) -> "AppPaths":
        """프로그램 본체와 분리된 현재 사용자 데이터 위치를 돌려준다.

        설치본은 Program Files에 놓이므로 쓰기 가능한 설정·DB·로그는 항상
        ``%LocalAppData%\\KiwoomMonitor\\data``에 보관한다. 개발 폴더 또는
        이전 설치본의 data 폴더에 있던 개인 데이터는 최초 한 번만 옮긴다.
        """
        app_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        base_dir = local_app_data / "KiwoomMonitor" / "data"
        log_dir = base_dir / "logs"
        base_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        cls._migrate_legacy_personal_data(app_root / "data", base_dir)
        cls._copy_default_sounds(app_root / "data" / "near_high_sounds", base_dir / "near_high_sounds")
        return cls(base_dir, log_dir, base_dir / "monitor.sqlite3")

    @staticmethod
    def _migrate_legacy_personal_data(legacy_dir: Path, data_dir: Path) -> None:
        """기존 개발/구형 설치 폴더의 개인 데이터만 안전하게 복사한다."""
        for filename in ("api.env", "monitor.sqlite3", "google_drive_client.json", "google_drive_token.dat"):
            source, destination = legacy_dir / filename, data_dir / filename
            if source.is_file() and not destination.exists():
                try:
                    shutil.copy2(source, destination)
                except OSError:
                    pass
        for directory in ("strength_icons", "near_high_icons", "near_high_sounds"):
            source, destination = legacy_dir / directory, data_dir / directory
            if not source.is_dir() or destination.exists():
                continue
            try:
                shutil.copytree(source, destination)
            except OSError:
                pass

    @staticmethod
    def _copy_default_sounds(source_dir: Path, destination_dir: Path) -> None:
        """설치 폴더의 기본 음성 파일을 사용자 데이터로 최초 한 번 복사한다."""
        for filename in ("interest.mp3", "caution.mp3", "fire.mp3"):
            source, destination = source_dir / filename, destination_dir / filename
            if source.is_file() and not destination.exists():
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                except OSError:
                    pass
