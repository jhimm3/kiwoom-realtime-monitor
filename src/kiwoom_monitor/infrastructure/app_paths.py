from __future__ import annotations

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
        app_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
        base_dir = (Path.home() / "Library" / "Application Support" / "kiwoom-monitor") if getattr(sys, "frozen", False) and sys.platform == "darwin" else app_root / "data"
        log_dir = base_dir / "logs"
        base_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        return cls(base_dir, log_dir, base_dir / "monitor.sqlite3")
