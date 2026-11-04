from __future__ import annotations

from kiwoom_monitor.domain.time_basis import kst_now
from kiwoom_monitor.domain.news_ai_contract import decode_news_ai_provenance

import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .backup_file import write_json_backup
from .news_ai_repository import NewsAIRepository, _inference_document
from kiwoom_monitor.domain.news_observation import stable_document_hash


class NewsAIBackupService:
    FORMAT = "kiwoom-realtime-monitor-news-ai"
    VERSION = 4

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        NewsAIRepository(database_path)

    def export_to(self, path: Path) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("BEGIN")
            columns = (
                "stock_code", "identity", "provider", "model", "summary", "category", "outlook",
                "confidence", "reason", "positive_evidence", "negative_evidence", "body_hash", "analyzed_at",
                "theme_candidates", "provenance_json",
            )
            rows = connection.execute(
                f"SELECT {','.join(columns)} FROM stock_news_ai ORDER BY analyzed_at"
            ).fetchall()
            shared_columns = (
                "identity", "provider", "model", "summary", "category", "positive_evidence",
                "negative_evidence", "company_impacts", "body_hash", "analyzed_at", "provenance_json",
            )
            shared_rows = connection.execute(
                f"SELECT {','.join(shared_columns)} FROM news_ai_shared ORDER BY analyzed_at"
            ).fetchall()
            inference_columns = ("inference_id", "content_hash", "computed_at", "available_at", "document_json")
            inference_rows = connection.execute(
                f"SELECT {','.join(inference_columns)} FROM news_ai_inferences ORDER BY inference_id"
            ).fetchall()
        finally:
            connection.close()
        document = {
            "format": self.FORMAT,
            "version": self.VERSION,
            "created_at": kst_now().isoformat(),
            "analyses": [dict(zip(columns, row, strict=True)) for row in rows],
            "shared_analyses": [dict(zip(shared_columns, row, strict=True)) for row in shared_rows],
            "inferences": [dict(zip(inference_columns, row, strict=True)) for row in inference_rows],
        }
        write_json_backup(path, document, indent=None, separators=(",", ":"))

    @classmethod
    def validate_file(cls, path: Path) -> None:
        cls._read_document(path)

    @classmethod
    def _read_document(cls, path: Path) -> dict[str, Any]:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("뉴스 AI 백업 파일을 읽을 수 없습니다.") from error
        if (not isinstance(document, dict) or document.get("format") != cls.FORMAT
                or type(document.get("version")) is not int or document.get("version") not in {1, 2, 3, 4}):
            raise ValueError("뉴스 AI 백업 파일 형식이 올바르지 않습니다.")
        analyses = document.get("analyses", [])
        if not isinstance(analyses, list):
            raise ValueError("뉴스 AI 분석 목록 형식이 올바르지 않습니다.")
        if document["version"] == 4:
            _validate_v4_document(document)
        return document

    def import_from(self, path: Path) -> int:
        document = self._read_document(path)
        if document["version"] == 4:
            return self._import_v4(document)
        analyses = document.get("analyses", [])
        valid: list[dict[str, object]] = []
        for item in analyses:
            if not isinstance(item, dict) or not item.get("stock_code") or not item.get("identity"):
                continue
            try:
                item["confidence"] = max(0, min(100, int(item.get("confidence", 0))))
            except (TypeError, ValueError):
                item["confidence"] = 0
            valid.append(item)
        shared = document.get("shared_analyses", [])
        valid_shared = [item for item in shared if isinstance(item, dict) and item.get("identity")] \
            if isinstance(shared, list) else []
        connection = sqlite3.connect(self._database_path)
        try:
            with connection:
                connection.executemany(
                    "INSERT INTO stock_news_ai(stock_code,identity,provider,model,summary,category,outlook,confidence,reason,positive_evidence,negative_evidence,body_hash,analyzed_at,provenance_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(stock_code,identity) DO UPDATE SET "
                    "provider=excluded.provider,model=excluded.model,summary=excluded.summary,category=excluded.category,"
                    "outlook=excluded.outlook,confidence=excluded.confidence,reason=excluded.reason,"
                    "positive_evidence=excluded.positive_evidence,negative_evidence=excluded.negative_evidence,"
                    "body_hash=excluded.body_hash,analyzed_at=excluded.analyzed_at,provenance_json=excluded.provenance_json",
                    [
                        (
                            str(item.get("stock_code", "")), str(item.get("identity", "")),
                            str(item.get("provider", "")), str(item.get("model", "")),
                            str(item.get("summary", "")), str(item.get("category", "")),
                            str(item.get("outlook", "")), int(item.get("confidence", 0)),
                            str(item.get("reason", "")), str(item.get("positive_evidence", "[]")),
                            str(item.get("negative_evidence", "[]")), str(item.get("body_hash", "")),
                            str(item.get("analyzed_at", kst_now().isoformat())),
                            json.dumps(decode_news_ai_provenance(item.get("provenance_json")), ensure_ascii=False, separators=(",", ":")),
                        )
                        for item in valid
                    ],
                )
                # Older backups lack candidates and must retain any already
                # present in the destination database.
                connection.executemany(
                    "UPDATE stock_news_ai SET theme_candidates=? WHERE stock_code=? AND identity=?",
                    [
                        (str(item["theme_candidates"]), str(item["stock_code"]), str(item["identity"]))
                        for item in valid if "theme_candidates" in item
                    ],
                )
                connection.executemany(
                    "INSERT INTO news_ai_shared(identity,provider,model,summary,category,positive_evidence,negative_evidence,company_impacts,body_hash,analyzed_at,provenance_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET provider=excluded.provider,model=excluded.model,"
                    "summary=excluded.summary,category=excluded.category,positive_evidence=excluded.positive_evidence,"
                    "negative_evidence=excluded.negative_evidence,company_impacts=excluded.company_impacts,body_hash=excluded.body_hash,analyzed_at=excluded.analyzed_at,provenance_json=excluded.provenance_json",
                    [(
                        str(item.get("identity", "")), str(item.get("provider", "")), str(item.get("model", "")),
                        str(item.get("summary", "")), str(item.get("category", "")),
                        str(item.get("positive_evidence", "[]")), str(item.get("negative_evidence", "[]")),
                        str(item.get("company_impacts", "[]")), str(item.get("body_hash", "")),
                        str(item.get("analyzed_at", kst_now().isoformat())),
                        json.dumps(decode_news_ai_provenance(item.get("provenance_json")), ensure_ascii=False, separators=(",", ":")),
                    ) for item in valid_shared],
                )
        finally:
            connection.close()
        return len(valid) + len(valid_shared)


    def _import_v4(self, document: dict[str, Any]) -> int:
        """Restore exact v4 values; legacy repair/default behavior stays separate."""
        connection = sqlite3.connect(self._database_path)
        try:
            with connection:
                for record in document["inferences"]:
                    values = tuple(record[name] for name in _INFERENCE_COLUMNS)
                    inserted = connection.execute(
                        "INSERT INTO news_ai_inferences(inference_id,content_hash,computed_at,available_at,document_json) "
                        "VALUES(?,?,?,?,?) ON CONFLICT(inference_id) DO NOTHING", values,
                    ).rowcount
                    if not inserted:
                        row = connection.execute(
                            "SELECT inference_id,content_hash,computed_at,available_at,document_json FROM news_ai_inferences WHERE inference_id=?",
                            (record["inference_id"],),
                        ).fetchone()
                        if row != values:
                            raise ValueError("뉴스 AI 추론 ID의 백업 원본이 기존 기록과 다릅니다.")
                for name, table, columns, primary in (
                    ("analyses", "stock_news_ai", _OWN_COLUMNS, ("stock_code", "identity")),
                    ("shared_analyses", "news_ai_shared", _SHARED_COLUMNS, ("identity",)),
                ):
                    updates = ",".join(f"{key}=excluded.{key}" for key in columns if key not in primary)
                    connection.executemany(
                        f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                        f"ON CONFLICT({','.join(primary)}) DO UPDATE SET {updates}",
                        [tuple(row[key] for key in columns) for row in document[name]],
                    )
        finally:
            connection.close()
        return len(document["analyses"]) + len(document["shared_analyses"])


_OWN_COLUMNS = (
    "stock_code", "identity", "provider", "model", "summary", "category", "outlook", "confidence", "reason",
    "positive_evidence", "negative_evidence", "body_hash", "analyzed_at", "theme_candidates", "provenance_json",
)
_SHARED_COLUMNS = (
    "identity", "provider", "model", "summary", "category", "positive_evidence", "negative_evidence",
    "company_impacts", "body_hash", "analyzed_at", "provenance_json",
)
_INFERENCE_COLUMNS = ("inference_id", "content_hash", "computed_at", "available_at", "document_json")


def _validate_v4_document(document: dict[str, Any]) -> None:
    """Check immutable raw/ref/index binding without reinterpreting old prompts."""
    rows = {}
    records = document.get("inferences")
    if not isinstance(records, list):
        raise ValueError("뉴스 AI 추론 백업 목록 형식이 올바르지 않습니다.")
    for record in records:
        if (not isinstance(record, dict) or any(key not in record for key in _INFERENCE_COLUMNS)
                or not isinstance(record["document_json"], str)):
            raise ValueError("뉴스 AI 추론 백업 행 형식이 올바르지 않습니다.")
        try:
            raw = _inference_document(json.loads(record["document_json"]))
        except (TypeError, ValueError) as error:
            raise ValueError("뉴스 AI 추론 백업 원본을 읽을 수 없습니다.") from error
        available = record["available_at"]
        if (record["inference_id"] != raw["inference_id"] or record["content_hash"] != stable_document_hash(raw)
                or record["computed_at"] != raw["computed_at"]
                or not isinstance(record["computed_at"], (int, float)) or isinstance(record["computed_at"], bool)
                or not isinstance(available, (int, float)) or isinstance(available, bool) or not math.isfinite(available)
                or record["inference_id"] in rows):
            raise ValueError("뉴스 AI 추론 백업 hash·ID·시각이 다릅니다.")
        rows[record["inference_id"]] = (record, raw)
    for name, columns in (("analyses", _OWN_COLUMNS), ("shared_analyses", _SHARED_COLUMNS)):
        values = document.get(name)
        if not isinstance(values, list):
            raise ValueError("뉴스 AI 결과 백업 목록 형식이 올바르지 않습니다.")
        seen = set()
        for value in values:
            if (not isinstance(value, dict) or any(key not in value for key in columns)
                    or any(not isinstance(value[key], str) for key in columns if key not in {"confidence", "provenance_json"})
                    or value["provenance_json"] is not None and not isinstance(value["provenance_json"], str)
                    or "confidence" in columns and type(value["confidence"]) is not int):
                raise ValueError("뉴스 AI 결과 백업 행 형식이 올바르지 않습니다.")
            identity = (value.get("stock_code"), value["identity"])
            if identity in seen:
                raise ValueError("뉴스 AI 결과 백업 identity가 중복입니다.")
            seen.add(identity)
            provenance = decode_news_ai_provenance(value["provenance_json"])
            reference = provenance.get("inference_ref")
            if not isinstance(reference, dict):
                if provenance.get("origin") == "repository_recorded":
                    raise ValueError("뉴스 AI 로컬 결과의 추론 참조가 백업에 없습니다.")
                continue
            inference_id = reference.get("inference_id")
            if not isinstance(inference_id, str):
                raise ValueError("뉴스 AI 결과 추론 ID의 형식이 다릅니다.")
            if inference_id not in rows:
                if provenance.get("origin") == "repository_recorded":
                    raise ValueError("뉴스 AI 로컬 결과의 추론 원본이 백업에 없습니다.")
                # Native and generic copies retain references, never fabricated local raw.
                continue
            record, raw = rows[inference_id]
            index = reference.get("input_index")
            if (type(index) is not int or not 0 <= index < len(raw["inputs"])
                    or reference.get("content_hash") != record["content_hash"]):
                raise ValueError("뉴스 AI 결과의 추론 참조 또는 입력 순서가 다릅니다.")
            source, output = raw["inputs"][index], raw["outputs"][index]
            if (value["identity"] != source["identity"] or value.get("stock_code", raw["target_id"]) != raw["target_id"]
                    or value["provider"] != raw["provider"] or value["model"] != raw["model"]
                    or value["body_hash"] != source["input_hash"]
                    or provenance.get("input_hash") != source["input_hash"]
                    or provenance.get("article_revision_id") != source["article_revision_id"]
                    or provenance.get("body_revision_id") != source["body_revision_id"]
                    or provenance.get("output_hash") != stable_document_hash(output)
                    or provenance.get("input_binding") != source["input_binding"]
                    or any(provenance.get(key) != raw[key] for key in ("target_id", "provider", "model", "prompt_version", "schema_version", "computed_at"))
                    or provenance.get("available_at") != record["available_at"]):
                raise ValueError("뉴스 AI 결과와 추론 원본의 결합이 다릅니다.")
            for key in ("summary", "category", "outlook", "confidence", "reason", "positive_evidence", "negative_evidence", "company_impacts", "theme_candidates"):
                if key not in value:
                    continue
                actual = value[key]
                if key in {"positive_evidence", "negative_evidence", "company_impacts", "theme_candidates"}:
                    try:
                        actual = json.loads(actual)
                    except (TypeError, ValueError) as error:
                        raise ValueError("뉴스 AI 결과 JSON 형식이 다릅니다.") from error
                if actual != output.get(key):
                    raise ValueError("뉴스 AI 결과값이 추론 원본과 다릅니다.")
