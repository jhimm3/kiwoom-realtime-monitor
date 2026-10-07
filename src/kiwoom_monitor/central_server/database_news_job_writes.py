"""News job cursor helpers shared by document, body and queue writers.

The caller owns the transaction; wake-up notification runs after its commit.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from kiwoom_monitor.domain.news_observation import news_job_key

# Preserve the existing wake-up log channel.
logger = logging.getLogger("kiwoom_monitor.central_server.database_documents")


def _notify_news_job_wakeup(store: Any) -> None:
    callback = getattr(store, "_news_job_wakeup", None)
    if callback is not None:
        try:
            callback()
        except Exception:
            # The enqueue has committed; a local wake failure must not change its result.
            logger.exception("뉴스 작업 wake-up 알림에 실패했습니다.")


def _insert_sqlite_news_job(connection: sqlite3.Connection, article_revision_id: str,
                            stock_code: str, target_id: str, stage: str, input_hash: str,
                            version: str, payload: dict[str, Any], now: float,
                            input_revision: str | None = None) -> None:
    key = news_job_key(stage, target_id, input_revision or article_revision_id, input_hash, version)
    connection.execute(
        "INSERT INTO central_news_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(job_key) DO NOTHING",
        (key, article_revision_id, stock_code, target_id, stage, input_hash, version, 0, now,
         "PENDING", "", "", json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now),
    )


def _insert_postgres_news_job(cursor: Any, article_revision_id: str, stock_code: str,
                              target_id: str, stage: str, input_hash: str, version: str,
                              payload: dict[str, Any], now: float,
                              input_revision: str | None = None) -> None:
    key = news_job_key(stage, target_id, input_revision or article_revision_id, input_hash, version)
    cursor.execute(
        "INSERT INTO central_news_jobs VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(job_key) DO NOTHING",
        (key, article_revision_id, stock_code, target_id, stage, input_hash, version, 0, now,
         "PENDING", "", "", json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now),
    )
