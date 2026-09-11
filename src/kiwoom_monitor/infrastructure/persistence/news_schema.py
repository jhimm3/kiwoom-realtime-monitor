"""뉴스 SQLite의 명시적 스키마 기준선과 향후 마이그레이션 원본."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.schema_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
)


NEWS_SCHEMA_VERSION = 1
NEWS_SCHEMA_BASELINE_NAME = "current_news_schema_baseline"


def initialize_news_schema(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        SQLiteMigrationRunner(connection, "news_schema_migrations").apply((
            SQLiteMigration(1, NEWS_SCHEMA_BASELINE_NAME, _apply_v1_baseline),
        ))
        connection.commit()
    finally:
        connection.close()


def _apply_v1_baseline(connection: sqlite3.Connection) -> None:
    statements = (
        "CREATE TABLE IF NOT EXISTS stock_news ("
        "stock_code TEXT NOT NULL, identity TEXT NOT NULL, title TEXT NOT NULL, "
        "description TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '', "
        "original_link TEXT NOT NULL DEFAULT '', published_at TEXT, "
        "relevant INTEGER NOT NULL DEFAULT 0, category TEXT NOT NULL DEFAULT '', "
        "outlook TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', "
        "relevance_score INTEGER NOT NULL DEFAULT 0, outlook_score INTEGER NOT NULL DEFAULT 0, "
        "first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY(stock_code, identity))",
        "CREATE TABLE IF NOT EXISTS stock_news_sync ("
        "stock_code TEXT PRIMARY KEY, checked_at TEXT NOT NULL, naver_checked_at TEXT)",
        "CREATE TABLE IF NOT EXISTS journal_news_links ("
        "group_id TEXT NOT NULL, stock_code TEXT NOT NULL, identity TEXT NOT NULL, "
        "linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY(group_id, stock_code, identity))",
        "CREATE TABLE IF NOT EXISTS stock_news_ai ("
        "stock_code TEXT NOT NULL, identity TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, "
        "summary TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', outlook TEXT NOT NULL, "
        "confidence INTEGER NOT NULL, reason TEXT NOT NULL, "
        "positive_evidence TEXT NOT NULL DEFAULT '[]', "
        "negative_evidence TEXT NOT NULL DEFAULT '[]', body_hash TEXT NOT NULL DEFAULT '', "
        "analyzed_at TEXT NOT NULL, PRIMARY KEY(stock_code, identity))",
        "CREATE TABLE IF NOT EXISTS news_ai_requests ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, requested_at TEXT NOT NULL, provider TEXT NOT NULL, "
        "model TEXT NOT NULL, request_mode TEXT NOT NULL, event_count INTEGER NOT NULL, "
        "article_count INTEGER NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, "
        "output_tokens INTEGER NOT NULL DEFAULT 0, total_tokens INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS news_ai_shared ("
        "identity TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, "
        "summary TEXT NOT NULL, category TEXT NOT NULL, positive_evidence TEXT NOT NULL, "
        "negative_evidence TEXT NOT NULL, company_impacts TEXT NOT NULL, "
        "body_hash TEXT NOT NULL, analyzed_at TEXT NOT NULL)",
    )
    for statement in statements:
        connection.execute(statement)
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(stock_news_sync)")
    }
    if "naver_checked_at" not in columns:
        connection.execute("ALTER TABLE stock_news_sync ADD COLUMN naver_checked_at TEXT")
