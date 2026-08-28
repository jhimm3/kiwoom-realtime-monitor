from __future__ import annotations

import sqlite3
from pathlib import Path

from .news_ai_repository import NewsAIRepository
from .stock_news_repository import StockNewsRepository


def initialize_news_database(news_database_path: Path) -> None:
    news_database_path.parent.mkdir(parents=True, exist_ok=True)
    StockNewsRepository(news_database_path)
    NewsAIRepository(news_database_path)


def migrate_legacy_news_database(main_database_path: Path, news_database_path: Path) -> None:
    """기존 메인 DB의 뉴스 표를 전용 DB로 한 번 복사한 뒤 제거한다."""
    initialize_news_database(news_database_path)
    source = sqlite3.connect(main_database_path)
    destination = sqlite3.connect(news_database_path)
    try:
        tables = {
            str(row[0]) for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('stock_news','stock_news_sync','stock_news_ai')"
            )
        }
        if not tables:
            return
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
        with source:
            for table in tables:
                source_count = int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                target_count = int(destination.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                if target_count >= source_count:
                    source.execute(f"DROP TABLE {table}")
    finally:
        destination.close()
        source.close()
