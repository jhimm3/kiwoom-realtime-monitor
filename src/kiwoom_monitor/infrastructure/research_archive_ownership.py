"""Mark files written by the application's research producers for after-close backup."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from kiwoom_monitor.domain.observation_archive import archive_json, archive_sha256
from kiwoom_monitor.infrastructure.persistence.backup_file import write_immutable_bytes


APP_RESEARCH_ARCHIVE_ROOT = "KIWOOM_MONITOR_APP_RESEARCH_ARCHIVE_ROOT"
APP_RESEARCH_OWNERS = "archive-owned"


def record_app_research_artifact(path: Path, *, state_dir: Path | None = None,
                                 database: Path | None = None, run_id: str = "", bundle: bool = False) -> None:
    """Record the published bytes without importing a user-selected input or folder."""
    if state_dir is None:
        configured = os.environ.get(APP_RESEARCH_ARCHIVE_ROOT)
        if not configured:
            return
        state_dir = Path(configured)
    try:
        path = path.resolve(strict=True)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        record = archive_json({
            "path": str(path), "sha256": digest, "bundle": bundle,
            "database": str(database.resolve(strict=True)) if database is not None else "", "run_id": run_id,
        })
        write_immutable_bytes(state_dir / APP_RESEARCH_OWNERS / (archive_sha256(record) + ".json"), record)
    except (OSError, ValueError, RuntimeError) as error:
        logging.getLogger(__name__).warning("연구 장후 보관 소유 기록 실패: %s", error)
