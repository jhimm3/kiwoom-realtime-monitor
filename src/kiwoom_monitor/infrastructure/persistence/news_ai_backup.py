from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .news_ai_repository import NewsAIRepository


class NewsAIBackupService:
    FORMAT = "kiwoom-realtime-monitor-news-ai"
    VERSION = 1

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        NewsAIRepository(database_path)

    def export_to(self, path: Path) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            columns = (
                "stock_code", "identity", "provider", "model", "summary", "category", "outlook",
                "confidence", "reason", "positive_evidence", "negative_evidence", "body_hash", "analyzed_at",
            )
            rows = connection.execute(
                f"SELECT {','.join(columns)} FROM stock_news_ai ORDER BY analyzed_at"
            ).fetchall()
        finally:
            connection.close()
        document = {
            "format": self.FORMAT,
            "version": self.VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "analyses": [dict(zip(columns, row, strict=True)) for row in rows],
        }
        path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    def import_from(self, path: Path) -> int:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("뉴스 AI 백업 파일을 읽을 수 없습니다.") from error
        if not isinstance(document, dict) or document.get("format") != self.FORMAT or document.get("version") != self.VERSION:
            raise ValueError("뉴스 AI 백업 파일 형식이 올바르지 않습니다.")
        analyses = document.get("analyses", [])
        if not isinstance(analyses, list):
            raise ValueError("뉴스 AI 분석 목록 형식이 올바르지 않습니다.")
        valid: list[dict[str, object]] = []
        for item in analyses:
            if not isinstance(item, dict) or not item.get("stock_code") or not item.get("identity"):
                continue
            try:
                item["confidence"] = max(0, min(100, int(item.get("confidence", 0))))
            except (TypeError, ValueError):
                item["confidence"] = 0
            valid.append(item)
        connection = sqlite3.connect(self._database_path)
        try:
            with connection:
                connection.executemany(
                    "INSERT INTO stock_news_ai(stock_code,identity,provider,model,summary,category,outlook,confidence,reason,positive_evidence,negative_evidence,body_hash,analyzed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(stock_code,identity) DO UPDATE SET "
                    "provider=excluded.provider,model=excluded.model,summary=excluded.summary,category=excluded.category,"
                    "outlook=excluded.outlook,confidence=excluded.confidence,reason=excluded.reason,"
                    "positive_evidence=excluded.positive_evidence,negative_evidence=excluded.negative_evidence,"
                    "body_hash=excluded.body_hash,analyzed_at=excluded.analyzed_at",
                    [
                        (
                            str(item.get("stock_code", "")), str(item.get("identity", "")),
                            str(item.get("provider", "")), str(item.get("model", "")),
                            str(item.get("summary", "")), str(item.get("category", "")),
                            str(item.get("outlook", "")), int(item.get("confidence", 0)),
                            str(item.get("reason", "")), str(item.get("positive_evidence", "[]")),
                            str(item.get("negative_evidence", "[]")), str(item.get("body_hash", "")),
                            str(item.get("analyzed_at", datetime.now(UTC).isoformat())),
                        )
                        for item in valid
                    ],
                )
        finally:
            connection.close()
        return len(valid)
