from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable


_LEGACY_FILES = (
    "api.env", "account-bindings.dat", "monitor.sqlite3",
    "google_drive_client.json", "google_drive_token.dat",
)
_LEGACY_DIRECTORIES = ("strength_icons", "near_high_icons", "near_high_sounds")
_DEFAULT_SOUNDS = ("interest.mp3", "caution.mp3", "fire.mp3")


def _query_root_is_unc(path: Path) -> bool:
    """Classify only explicit Windows UNC syntax, without probing a drive or host."""
    drive = PureWindowsPath(path).drive
    if drive.startswith("\\\\?\\"):
        return drive[4:].casefold().startswith("unc\\")
    return drive.startswith("\\\\") and not drive.startswith("\\\\.\\")


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    log_dir: Path
    database_path: Path

    @property
    def central_database_path(self) -> Path:
        """Full-root native central data/execution store, separate from UI settings."""
        return self.data_dir / "central.sqlite3"

    @property
    def news_database_path(self) -> Path:
        """메인 실시간 DB와 물리적으로 분리된 뉴스 전용 DB."""
        return self.data_dir / "news.sqlite3"

    @property
    def journal_database_path(self) -> Path:
        """실시간 표와 SQLite 잠금을 공유하지 않는 매매일지 전용 DB."""
        return self.data_dir / "journal.sqlite3"

    @property
    def account_binding_path(self) -> Path:
        """현재 사용자에게만 복호화되는 검증 계좌 binding mirror."""
        return self.data_dir / "account-bindings.dat"

    @staticmethod
    def user_state_root() -> Path:
        if sys.platform == 'darwin':
            return Path.home() / 'Library' / 'Application Support' / 'KiwoomMonitor'
        if sys.platform.startswith('linux'):
            state_root = Path(os.environ.get('XDG_DATA_HOME', ''))
            if not state_root.is_absolute():
                state_root = Path.home() / '.local' / 'share'
            return state_root / 'KiwoomMonitor'
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'KiwoomMonitor'

    @classmethod
    def for_current_user(cls) -> "AppPaths":
        """Resolve/create the default folders without importing personal data.

        A source/editable checkout keeps its project data. Installed wheels and
        frozen desktops use the platform's writable user-data directory.
        Explicit desktop preparation belongs inside the root restore lease.
        """
        frozen = getattr(sys, "frozen", False)
        module_path = Path(__file__).resolve()
        app_root = module_path.parents[3]
        source_checkout = (
            not frozen
            and module_path == app_root / "src" / "kiwoom_monitor" / "infrastructure" / "app_paths.py"
            and (app_root / "pyproject.toml").is_file()
        )
        if source_checkout:
            base_dir = app_root / "data"
        else:
            base_dir = cls.user_state_root() / "data"
        log_dir = base_dir / "logs"
        base_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        return cls(base_dir, log_dir, base_dir / "monitor.sqlite3")

    @classmethod
    def for_query_role(cls, data_dir: Path) -> "AppPaths":
        """Select only the explicitly named local role root, without importing data."""
        return cls._for_desktop_role(data_dir, database_name="query-role.sqlite3", label="조회 역할")

    @classmethod
    def for_full_role(cls, data_dir: Path) -> "AppPaths":
        """Select a new full desktop root without adopting the default data."""
        return cls._for_desktop_role(data_dir, database_name="monitor.sqlite3", label="전체 앱 역할")

    @classmethod
    def _for_desktop_role(cls, data_dir: Path, *, database_name: str, label: str) -> "AppPaths":
        if _query_root_is_unc(data_dir):
            raise ValueError(f"{label}의 SQLite DB는 UNC 공유 폴더에서 열 수 없습니다.")
        root = data_dir.expanduser().absolute()
        if root.resolve() != root or root.is_symlink():
            raise ValueError(f"{label} 폴더는 우회되지 않은 로컬 경로여야 합니다.")
        root.mkdir(parents=True, exist_ok=True)
        log_dir = root / "logs"
        log_dir.mkdir(exist_ok=True)
        return cls(root, log_dir, root / database_name)

    def desktop_startup_preparation(
        self, *, query_only: bool = False, copy_legacy_personal: bool = True,
    ) -> Callable[[], None] | None:
        """Observe missing frozen files; the returned action requires the root lock.

        Re-evaluating this method is copy-free. The action rechecks destinations
        through the existing compatibility helpers while exclusively owned.
        A restore ledger takes priority over this action in the coordinator.
        """
        if not getattr(sys, "frozen", False):
            return None
        legacy_dir = Path(sys.executable).resolve().parent / "data"
        personal_work = not query_only and copy_legacy_personal and (
            any((legacy_dir / name).is_file() and not (self.data_dir / name).exists()
                for name in _LEGACY_FILES)
            or any((legacy_dir / name).is_dir() and not (self.data_dir / name).exists()
                   and self._legacy_directory_has_personal_data(legacy_dir / name)
                   for name in _LEGACY_DIRECTORIES)
        )
        sound_source = legacy_dir / "near_high_sounds"
        sound_destination = self.data_dir / "near_high_sounds"
        sound_work = any((sound_source / name).is_file() and not (sound_destination / name).exists()
                         for name in _DEFAULT_SOUNDS)
        if not personal_work and not sound_work:
            return None

        def prepare() -> None:
            if not query_only and copy_legacy_personal:
                self._migrate_legacy_personal_data(legacy_dir, self.data_dir)
            self._copy_default_sounds(sound_source, sound_destination)

        return prepare

    @staticmethod
    def _migrate_legacy_personal_data(legacy_dir: Path, data_dir: Path) -> None:
        """기존 개발/구형 설치 폴더의 개인 데이터만 안전하게 복사한다."""
        for filename in _LEGACY_FILES:
            source, destination = legacy_dir / filename, data_dir / filename
            if source.is_file() and not destination.exists():
                AppPaths._copy_legacy_asset(source, destination, directory=False)
        for directory in _LEGACY_DIRECTORIES:
            source, destination = legacy_dir / directory, data_dir / directory
            if not source.is_dir() or destination.exists():
                continue
            if not AppPaths._legacy_directory_has_personal_data(source):
                continue
            AppPaths._copy_legacy_asset(source, destination, directory=True)

    @staticmethod
    def _legacy_directory_has_personal_data(source: Path) -> bool:
        # A packaged folder with only the three known public sounds belongs to
        # the separate best-effort default helper, including on full startup.
        return source.name != "near_high_sounds" or any(
            item.name not in _DEFAULT_SOUNDS or not item.is_file()
            for item in source.iterdir()
        )

    @staticmethod
    def _copy_legacy_asset(source: Path, destination: Path, *, directory: bool) -> None:
        """Publish a completed private copy while the startup root is exclusive.

        A failed copy must remain absent at the final path, so a restart retries
        instead of treating a partial file/directory as accepted user data.
        """
        temporary = Path(tempfile.mkdtemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp",
        ))
        try:
            staged = temporary / destination.name
            if directory:
                shutil.copytree(source, staged)
            else:
                shutil.copy2(source, staged)
            # Other cooperating writers share the exclusive root lease. Retain
            # a destination that appeared since planning instead of overwriting.
            if not destination.exists():
                staged.rename(destination)
        except BaseException:
            try:
                shutil.rmtree(temporary)
            except OSError:
                # Cleanup must not replace the original copy/publish failure.
                # A leftover staging directory is never a completed destination.
                pass
            raise
        else:
            shutil.rmtree(temporary)

    @staticmethod
    def _copy_default_sounds(source_dir: Path, destination_dir: Path) -> None:
        """설치 폴더의 기본 음성 파일을 사용자 데이터로 최초 한 번 복사한다."""
        for filename in _DEFAULT_SOUNDS:
            source, destination = source_dir / filename, destination_dir / filename
            if source.is_file() and not destination.exists():
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                except OSError:
                    pass
