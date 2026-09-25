from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .news_schema import initialize_news_schema


def initialize_news_database(news_database_path: Path) -> None:
    initialize_news_schema(news_database_path)


def migrate_legacy_news_database(main_database_path: Path, news_database_path: Path) -> None:
    """기존 메인 DB의 뉴스 표를 전용 DB로 한 번 복사한 뒤 제거한다."""
    news_existed = news_database_path.is_file()
    source = sqlite3.connect(main_database_path)
    try:
        ledger = source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='legacy_news_transfer_migrations'"
        ).fetchone()
        if not ledger:
            raise RuntimeError("메인 DB 뉴스 이관 원장이 없습니다. 메인 DB v8 초기화가 필요합니다.")
        if source.execute(
            "SELECT 1 FROM legacy_news_transfer_migrations WHERE version=1"
        ).fetchone():
            if not news_existed:
                raise RuntimeError("완료된 뉴스 DB 이관의 대상 파일이 없습니다.")
            reappeared = source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('stock_news','stock_news_sync','stock_news_ai') LIMIT 1"
            ).fetchone()
            if reappeared:
                raise RuntimeError("이관 완료 후 메인 DB에 뉴스 표가 다시 생성됐습니다.")
            initialize_news_database(news_database_path)
            return
        initialize_news_database(news_database_path)
        tables = {
            str(row[0]) for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('stock_news','stock_news_sync','stock_news_ai')"
            )
        }
        destination = sqlite3.connect(news_database_path)
        try:
            with destination:
                for table in ("stock_news", "stock_news_sync", "stock_news_ai"):
                    if table not in tables:
                        continue
                    columns = [str(row[1]) for row in source.execute(f"PRAGMA table_info({table})")]
                    rows = source.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
                    if rows:
                        placeholders = ",".join("?" for _ in columns)
                        destination.executemany(
                            f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES ({placeholders})",
                            rows,
                        )
            # 복사 건수를 확인한 표만 원본에서 제거한다. 전용 DB가 곧 복구본이다.
            verified = all(
                int(destination.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                >= int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            )
        finally:
            destination.close()
        if not verified:
            return
        with source:
            for table in tables:
                source.execute(f"DROP TABLE {table}")
            source.execute(
                "INSERT INTO legacy_news_transfer_migrations(version,name,completed_at) VALUES(1,?,?)",
                ("main_news_tables_to_news_database", datetime.now(UTC).isoformat()),
            )
    finally:
        source.close()
