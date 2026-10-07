"""뉴스 SQLite의 명시적 스키마 기준선과 향후 마이그레이션 원본."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.schema_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
)


NEWS_SCHEMA_VERSION = 4
NEWS_SCHEMA_BASELINE_NAME = "current_news_schema_baseline"
NEWS_ACCOUNT_SCOPE_NAME = "account_scoped_journal_news_links"
NEWS_LINK_TOMBSTONE_NAME = "journal_news_link_tombstones"
NEWS_AI_THEME_CANDIDATES_NAME = "news_ai_theme_candidates"


def initialize_news_schema(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        plan = (
            SQLiteMigration(1, NEWS_SCHEMA_BASELINE_NAME, _apply_v1_baseline),
            SQLiteMigration(2, NEWS_ACCOUNT_SCOPE_NAME, _apply_v2_account_scope),
            SQLiteMigration(3, NEWS_LINK_TOMBSTONE_NAME, _apply_v3_link_tombstones),
            SQLiteMigration(4, NEWS_AI_THEME_CANDIDATES_NAME, _apply_v4_ai_theme_candidates),
        )
        # 완성된 DB는 시작할 때 읽기 전용 버전 확인만 한다. 새 버전이나
        # 불완전한 원장은 아래 실행기가 기존 트랜잭션/호환성 검사를 맡는다.
        try:
            recorded = connection.execute(
                "SELECT version,name FROM news_schema_migrations ORDER BY version"
            ).fetchall()
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error).lower():
                raise
            recorded = []
        if recorded == [(migration.version, migration.name) for migration in plan]:
            return
        SQLiteMigrationRunner(connection, "news_schema_migrations").apply(plan)
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


def _apply_v2_account_scope(connection: sqlite3.Connection) -> None:
    """기존 연결은 legacy로 보존하고 이후 연결은 계좌별로 분리한다."""
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(journal_news_links)")
    }
    if "origin_broker" in columns:
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_news_links_scope_group ON journal_news_links("
            "canonical_account_ref,origin_broker,origin_environment,group_id,stock_code)"
        )
        return
    connection.execute("ALTER TABLE journal_news_links RENAME TO journal_news_links_v1")
    connection.execute(
        "CREATE TABLE journal_news_links ("
        "group_id TEXT NOT NULL, stock_code TEXT NOT NULL, identity TEXT NOT NULL, "
        "linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "origin_broker TEXT NOT NULL, origin_environment TEXT NOT NULL, "
        "origin_account_ref TEXT NOT NULL, canonical_account_ref TEXT NOT NULL, "
        "PRIMARY KEY(origin_broker, origin_environment, origin_account_ref, "
        "group_id, stock_code, identity))"
    )
    connection.execute(
        "INSERT INTO journal_news_links("
        "group_id,stock_code,identity,linked_at,origin_broker,origin_environment,"
        "origin_account_ref,canonical_account_ref) "
        "SELECT group_id,stock_code,identity,linked_at,'legacy','unknown',"
        "'legacy-unassigned','legacy-unassigned' FROM journal_news_links_v1"
    )
    connection.execute("DROP TABLE journal_news_links_v1")
    connection.execute(
        "CREATE INDEX idx_journal_news_links_scope_group ON journal_news_links("
        "canonical_account_ref,origin_broker,origin_environment,group_id,stock_code)"
    )


def _apply_v3_link_tombstones(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(journal_news_links)")
    }
    additions = (
        ("is_deleted", "INTEGER NOT NULL DEFAULT 0"),
        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ("source_collection", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("source_owner", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("source_key", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("source_content_hash", "TEXT NOT NULL DEFAULT 'unknown'"),
    )
    for name, definition in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE journal_news_links ADD COLUMN {name} {definition}")
    connection.execute(
        "UPDATE journal_news_links SET updated_at=linked_at WHERE updated_at=''"
    )


def _apply_v4_ai_theme_candidates(connection: sqlite3.Connection) -> None:
    for table in ("stock_news_ai", "news_ai_shared"):
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if "theme_candidates" not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN theme_candidates TEXT NOT NULL DEFAULT '[]'"
            )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_journal_news_links_active_scope ON journal_news_links("
        "canonical_account_ref,origin_broker,origin_environment,group_id,stock_code,is_deleted)"
    )
