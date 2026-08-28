from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis, AIRequestUsage


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
            connection.execute(
                "CREATE TABLE IF NOT EXISTS news_ai_requests ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, requested_at TEXT NOT NULL, provider TEXT NOT NULL, "
                "model TEXT NOT NULL, request_mode TEXT NOT NULL, event_count INTEGER NOT NULL, article_count INTEGER NOT NULL, "
                "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, total_tokens INTEGER NOT NULL DEFAULT 0)"
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

    def load_many(
        self, stock_code: str, items: tuple[StockNewsItem, ...],
    ) -> dict[str, StoredAINewsAnalysis]:
        """한 종목의 저장된 AI 결과를 DB 연결 한 번으로 읽는다."""
        identities = tuple(dict.fromkeys(news_identity(item) for item in items))
        if not identities:
            return {}
        placeholders = ",".join("?" for _ in identities)
        connection = sqlite3.connect(self._database_path)
        try:
            rows = connection.execute(
                "SELECT identity, provider, model, summary, outlook, confidence, reason, "
                "positive_evidence, negative_evidence, analyzed_at, category "
                f"FROM stock_news_ai WHERE stock_code=? AND identity IN ({placeholders})",
                (stock_code, *identities),
            ).fetchall()
        finally:
            connection.close()
        return {
            str(row[0]): StoredAINewsAnalysis(
                AINewsAnalysis(str(row[3]), str(row[4]), int(row[5]), str(row[6]),
                               tuple(json.loads(row[7])), tuple(json.loads(row[8])), str(row[10])),
                str(row[1]), str(row[2]), datetime.fromisoformat(str(row[9])),
            )
            for row in rows
        }

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
                "SELECT COUNT(*) FROM news_ai_requests WHERE substr(requested_at, 1, 10)=?", (today,)
            ).fetchone()
        finally:
            connection.close()
        return int(row[0]) if row else 0

    def log_request(self, provider: str, model: str, request_mode: str, event_count: int,
                    article_count: int, usage: AIRequestUsage) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute(
                "INSERT INTO news_ai_requests(requested_at,provider,model,request_mode,event_count,article_count,input_tokens,output_tokens,total_tokens) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now().astimezone().isoformat(), provider, model, request_mode, event_count, article_count,
                 usage.input_tokens, usage.output_tokens, usage.total_tokens),
            )
            connection.commit()
        finally:
            connection.close()

    def daily_usage(self) -> tuple[int, int, int, int]:
        today = datetime.now().astimezone().date().isoformat()
        connection = sqlite3.connect(self._database_path)
        try:
            row = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(total_tokens),0) "
                "FROM news_ai_requests WHERE substr(requested_at,1,10)=?", (today,),
            ).fetchone()
        finally:
            connection.close()
        return tuple(map(int, row or (0, 0, 0, 0)))
