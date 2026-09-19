"""매매일지 전용 DB의 안전한 선택 백업·복원."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .journal_schema import JOURNAL_SCHEMA_VERSION


class JournalBackupService:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def export_to(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._database_path)) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)

    def import_from(self, source_path: Path) -> None:
        if not source_path.is_file():
            raise ValueError("매매일지 백업 파일을 찾을 수 없습니다.")
        with closing(sqlite3.connect(source_path)) as source:
            required = {str(row[0]) for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"trade_fills", "journal_minute_bars", "trade_reviews"} <= required:
                raise ValueError("이 프로그램의 매매일지 백업 파일이 아닙니다.")
            if "journal_schema_migrations" in required:
                row = source.execute(
                    "SELECT MAX(version) FROM journal_schema_migrations"
                ).fetchone()
                version = int(row[0] or 0)
                if version > JOURNAL_SCHEMA_VERSION:
                    raise ValueError(
                        f"더 최신 매매일지 DB입니다. 지원 버전: {JOURNAL_SCHEMA_VERSION}, 백업 버전: {version}"
                    )
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._database_path)) as destination:
                source.backup(destination)
