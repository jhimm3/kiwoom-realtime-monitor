from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis


@dataclass(frozen=True)
class StoredAINewsAnalysis:
    analysis: AINewsAnalysis
    provider: str
    model: str
    analyzed_at: datetime


def news_identity(item: StockNewsItem) -> str:
    return item.original_link or item.link or f"{item.published_at!s}|{item.title}"


class NewsAIRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS stock_news_ai ("
                "stock_code TEXT NOT NULL, identity TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, "
                "summary TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', outlook TEXT NOT NULL, confidence INTEGER NOT NULL, "
                "reason TEXT NOT NULL, positive_evidence TEXT NOT NULL DEFAULT '[]', negative_evidence TEXT NOT NULL DEFAULT '[]', "
                "body_hash TEXT NOT NULL DEFAULT '', analyzed_at TEXT NOT NULL, PRIMARY KEY(stock_code, identity))"
            )
            connection.commit()
        finally:
            connection.close()

    def load(self, stock_code: str, item: StockNewsItem) -> StoredAINewsAnalysis | None:
        connection = sqlite3.connect(self._database_path)
        try:
            row = connection.execute(
                "SELECT provider, model, summary, outlook, confidence, reason, positive_evidence, negative_evidence, analyzed_at, category "
                "FROM stock_news_ai WHERE stock_code=? AND identity=?",
                (stock_code, news_identity(item)),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return StoredAINewsAnalysis(
            AINewsAnalysis(str(row[2]), str(row[3]), int(row[4]), str(row[5]),
                           tuple(json.loads(row[6])), tuple(json.loads(row[7])), str(row[9])),
            str(row[0]), str(row[1]), datetime.fromisoformat(str(row[8])),
        )

    def save(self, stock_code: str, item: StockNewsItem, provider: str, model: str,
             body_hash: str, analysis: AINewsAnalysis) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute(
                "INSERT INTO stock_news_ai(stock_code, identity, provider, model, summary, category, outlook, confidence, reason, positive_evidence, negative_evidence, body_hash, analyzed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(stock_code, identity) DO UPDATE SET "
                "provider=excluded.provider, model=excluded.model, summary=excluded.summary, outlook=excluded.outlook, "
                "category=excluded.category, "
                "confidence=excluded.confidence, reason=excluded.reason, positive_evidence=excluded.positive_evidence, "
                "negative_evidence=excluded.negative_evidence, body_hash=excluded.body_hash, analyzed_at=excluded.analyzed_at",
                (stock_code, news_identity(item), provider, model, analysis.summary, analysis.category, analysis.outlook,
                 analysis.confidence, analysis.reason, json.dumps(analysis.positive_evidence, ensure_ascii=False),
                 json.dumps(analysis.negative_evidence, ensure_ascii=False), body_hash, datetime.now(UTC).isoformat()),
            )
            connection.commit()
        finally:
            connection.close()

    def daily_count(self) -> int:
        today = datetime.now().astimezone().date().isoformat()
        connection = sqlite3.connect(self._database_path)
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM stock_news_ai WHERE substr(analyzed_at, 1, 10)=?", (today,)
            ).fetchone()
        finally:
            connection.close()
        return int(row[0]) if row else 0
