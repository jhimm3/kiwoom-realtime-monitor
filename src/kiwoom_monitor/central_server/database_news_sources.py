"""News source pages, collection progress, diagnostics and stored market feeds.

Page helpers borrow the caller's cursor, including historical batch importers.
Store methods own the same native transactions and post-commit wake-up as before.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from time import time
from typing import Any
from zoneinfo import ZoneInfo

from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION, SUPPLY_CONTRACT_RULE_VERSION, stable_document_hash,
)
from .database_codec import bounded_limit
from .database_news_job_writes import (
    _insert_sqlite_news_job, _insert_postgres_news_job, _notify_news_job_wakeup,
)


_NEWS_SOURCE_CURSOR_KEYS = (
    "source_id", "scope", "query_text", "cursor_published_at", "cursor_identity",
    "pending_published_at", "pending_identity", "next_start", "next_schedule_at",
    "checked_at", "last_success", "coverage", "truncated", "error", "updated_at",
)


class SQLiteNewsSourceStoreMixin:
    def load_news_source_cursor(self, source_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT source_id,scope,query_text,cursor_published_at,cursor_identity,pending_published_at,"
                "pending_identity,next_start,next_schedule_at,checked_at,last_success,coverage,truncated,error,updated_at "
                "FROM central_news_source_cursors WHERE source_id=?", (source_id,),
            ).fetchone()
        return _news_source_cursor(row) if row else None

    def save_news_source_page(self, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            result = _save_sqlite_news_source_page(connection, value)
        if value.get("items"):
            _notify_news_job_wakeup(self)
        return result

    def load_news_source_diagnostics(self, *, source_id: str = "", days: int = 7,
                                     limit: int = 100) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            return _load_sqlite_news_source_diagnostics(connection, source_id, days, limit)

    def load_market_news_feed(self, source: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(_market_news_feed_sql("?"),
                                      (_market_news_source_prefix(source),
                                       bounded_limit(limit, 1000))).fetchall()
        return _decode_market_news_feed(rows)


class PostgresNewsSourceStoreMixin:
    def load_news_source_cursor(self, source_id: str) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.news_source",
            writer_kind="source_cursor",
            operation="load_news_source_cursor",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_id,scope,query_text,cursor_published_at,cursor_identity,pending_published_at,"
                "pending_identity,next_start,next_schedule_at,checked_at,last_success,coverage,truncated,error,updated_at "
                "FROM central_news_source_cursors WHERE source_id=%s", (source_id,),
            )
            row = cursor.fetchone()
        return _news_source_cursor(row) if row else None

    def save_news_source_page(self, value: dict[str, Any]) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        scope = str(value.get("scope") or "query_set")
        items = value.get("items", [])
        writer = DBWriterContext(
            writer_family="news.source_page", writer_kind=f"news_source:{scope}",
            operation="save_news_source_page",
            rows_attempted=len(items) if isinstance(items, (list, tuple)) else None,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            result = _save_postgres_news_source_page(cursor, value)
        if items:
            _notify_news_job_wakeup(self)
        return result

    def load_news_source_diagnostics(self, *, source_id: str = "", days: int = 7,
                                     limit: int = 100) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.news_source",
            writer_kind="source_diagnostics",
            operation="load_news_source_diagnostics",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            return _load_postgres_news_source_diagnostics(cursor, source_id, days, limit)

    def load_market_news_feed(self, source: str, *, limit: int = 200) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.news_publications",
            writer_kind="market_feed",
            operation="load_market_news_feed",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute(_market_news_feed_sql("%s"),
                           (_market_news_source_prefix(source),
                            bounded_limit(limit, 1000)))
            rows = cursor.fetchall()
        return _decode_market_news_feed(rows)


def _news_source_cursor(row: tuple[object, ...]) -> dict[str, Any]:
    result = dict(zip(_NEWS_SOURCE_CURSOR_KEYS, row, strict=True))
    result["next_start"] = int(result["next_start"])
    result["truncated"] = bool(result["truncated"])
    return result


def _source_item(
    value: dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, Any]], bool]:
    identity = str(value.get("identity") or "").strip()
    document = value.get("document")
    if not identity or not isinstance(document, dict):
        raise ValueError("소스 관측에는 identity와 기사 문서가 필요합니다.")
    targets = value.get("targets")
    return (
        identity, dict(document), list(targets) if isinstance(targets, list) else [],
        bool(value.get("processing_excluded", False)),
    )


def _news_article_content_hash(document: dict[str, Any]) -> str:
    # 언론사명·도메인은 URL에서 다시 계산할 수 있는 검색 편의 필드다. 이 값의
    # 추가나 매핑 보정만으로 원문 기사 리비전을 새로 만들지 않는다.
    content = {
        key: value for key, value in document.items()
        if key not in {"publisher_domain", "publisher_name"}
    }
    return stable_document_hash(content)


def _save_sqlite_news_source_page(connection: sqlite3.Connection,
                                  value: dict[str, Any]) -> dict[str, Any]:
    now = time()
    source_id, query_text = str(value["source_id"]), str(value["query_text"])
    run_id, page_start = str(value.get("run_id") or uuid.uuid4().hex), int(value.get("page_start") or 1)
    new_count, duplicate_count = 0, 0
    for raw in value.get("items", []):
        identity, document, targets, processing_excluded = _source_item(raw)
        document.update({"identity": identity, "stock_code": "GLOBAL"})
        content_hash = _news_article_content_hash(document)
        source_latest = connection.execute(
            "SELECT article_revision_id,content_hash FROM central_news_source_observations "
            "WHERE source_id=? AND identity=? ORDER BY accepted_sequence DESC LIMIT 1",
            (source_id, identity),
        ).fetchone()
        latest = connection.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code='GLOBAL' AND identity=? ORDER BY accepted_sequence DESC LIMIT 1",
            (identity,),
        ).fetchone()
        reusable = source_latest if source_latest and str(source_latest[1]) == content_hash else (
            latest if latest and str(latest[1]) == content_hash else None
        )
        if reusable:
            article_revision_id, duplicate = str(reusable[0]), True
            duplicate_count += 1
        else:
            article_revision_id, duplicate = uuid.uuid4().hex, False
            available_at = time()
            connection.execute(
                "INSERT INTO central_news_article_revisions(article_revision_id,stock_code,identity,content_hash,"
                "collector_id,published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (article_revision_id, "GLOBAL", identity, content_hash, "naver", document.get("published_at"),
                 now, available_at, str(value.get("scope") or "query_set"), str(latest[0]) if latest else None,
                 json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
            )
            new_count += 1
        if not processing_excluded:
            _insert_sqlite_news_job(
                connection, article_revision_id, "GLOBAL", "GLOBAL", "BODY", content_hash,
                ARTICLE_BODY_EXTRACTOR_VERSION, document, time(),
            )
        for target in targets:
            _insert_sqlite_article_target(connection, article_revision_id, identity, target)
            if not processing_excluded and str(target.get("relation_status") or "") == "confirmed":
                _enqueue_sqlite_existing_body_rule(connection, article_revision_id, target)
        connection.execute(
            "INSERT INTO central_news_source_observations(observation_id,run_id,source_id,query_text,page_start,"
            "article_revision_id,identity,published_at,received_at,available_at,content_hash,duplicate,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, run_id, source_id, query_text, page_start, article_revision_id, identity,
             document.get("published_at"), now, time(), content_hash, int(duplicate),
             json.dumps({"query_membership": query_text, "targets": targets,
                         "processing_excluded": processing_excluded, **document},
                        ensure_ascii=False, separators=(",", ":"))),
        )
    raw_count = len(value.get("items", []))
    completed = float(value.get("completed_at") or time())
    run_document = dict(value.get("document") or {})
    connection.execute(
        "INSERT INTO central_news_source_runs(run_revision_id,run_id,source_id,scope,page_start,checked_at,completed_at,"
        "raw_count,unique_count,duplicate_count,request_count,budget_remaining,truncated,coverage,error,document_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, run_id, source_id, str(value.get("scope") or "query_set"), page_start,
         float(value.get("checked_at") or now), completed, raw_count, new_count, duplicate_count,
         int(value.get("request_count") or 0), int(value.get("budget_remaining") or 0),
         int(bool(value.get("truncated"))), str(value.get("coverage") or "query_set"),
         str(value.get("error") or "")[:1000], json.dumps(run_document, ensure_ascii=False, separators=(",", ":"))),
    )
    _upsert_sqlite_source_cursor(connection, value, now)
    return {"raw_count": raw_count, "unique_count": new_count, "duplicate_count": duplicate_count}


def _insert_sqlite_article_target(connection: sqlite3.Connection, article_revision_id: str,
                                  identity: str, target: dict[str, Any]) -> bool:
    code = str(target.get("stock_code") or "") or None
    name = str(target.get("stock_name") or "") or None
    status = str(target.get("relation_status") or "unresolved")
    version = str(target.get("rule_version") or "exact-krx-company-name-v1")
    exists = connection.execute(
        "SELECT target_revision_id FROM central_news_article_target_revisions WHERE article_revision_id=? "
        "AND COALESCE(stock_code,'')=? AND COALESCE(stock_name,'')=? AND relation_status=? AND rule_version=?",
        (article_revision_id, code or "", name or "", status, version),
    ).fetchone()
    if exists:
        return False
    previous = connection.execute(
        "SELECT target_revision_id FROM central_news_article_target_revisions WHERE identity=? "
        "AND COALESCE(stock_code,'')=? ORDER BY accepted_sequence DESC LIMIT 1", (identity, code or ""),
    ).fetchone()
    document = dict(target)
    connection.execute(
        "INSERT INTO central_news_article_target_revisions(target_revision_id,article_revision_id,identity,stock_code,"
        "stock_name,relation_status,evidence_text,rule_version,available_at,revision_of,document_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, article_revision_id, identity, code, name, status,
         str(target.get("evidence_text") or ""), version, time(), str(previous[0]) if previous else None,
         json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
    )
    return True


def _enqueue_sqlite_existing_body_rule(
    connection: sqlite3.Connection, article_revision_id: str, target: dict[str, Any],
) -> None:
    body = connection.execute(
        "SELECT body_revision_id,content_hash FROM central_news_body_revisions "
        "WHERE article_revision_id=? AND status IN ('fulltext','summary_only') "
        "ORDER BY accepted_sequence DESC LIMIT 1", (article_revision_id,),
    ).fetchone()
    target_code = str(target.get("stock_code") or "")
    if body is None or not target_code:
        return
    _insert_sqlite_news_job(
        connection, article_revision_id, "GLOBAL", target_code, "RULE", str(body[1]),
        SUPPLY_CONTRACT_RULE_VERSION,
        {"body_revision_id": str(body[0]), "stock_code": target_code,
         "stock_name": str(target.get("stock_name") or target_code)},
        time(), str(body[0]),
    )


def _upsert_sqlite_source_cursor(connection: sqlite3.Connection, value: dict[str, Any], now: float) -> None:
    connection.execute(
        "INSERT INTO central_news_source_cursors(source_id,scope,query_text,cursor_published_at,cursor_identity,"
        "pending_published_at,pending_identity,next_start,next_schedule_at,checked_at,last_success,coverage,truncated,error,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET scope=excluded.scope,"
        "query_text=excluded.query_text,cursor_published_at=excluded.cursor_published_at,cursor_identity=excluded.cursor_identity,"
        "pending_published_at=excluded.pending_published_at,pending_identity=excluded.pending_identity,next_start=excluded.next_start,"
        "next_schedule_at=excluded.next_schedule_at,checked_at=excluded.checked_at,last_success=excluded.last_success,"
        "coverage=excluded.coverage,truncated=excluded.truncated,error=excluded.error,updated_at=excluded.updated_at",
        (str(value["source_id"]), str(value.get("scope") or "query_set"), str(value["query_text"]),
         value.get("cursor_published_at"), str(value.get("cursor_identity") or ""),
         value.get("pending_published_at"), str(value.get("pending_identity") or ""),
         int(value.get("next_start") or 1), float(value.get("next_schedule_at") or now),
         float(value.get("checked_at") or now), value.get("last_success"),
         str(value.get("coverage") or "query_set"), int(bool(value.get("truncated"))),
         str(value.get("error") or "")[:1000], now),
    )


def _save_postgres_news_source_page(cursor: Any, value: dict[str, Any]) -> dict[str, Any]:
    now = time()
    source_id, query_text = str(value["source_id"]), str(value["query_text"])
    run_id, page_start = str(value.get("run_id") or uuid.uuid4().hex), int(value.get("page_start") or 1)
    new_count, duplicate_count = 0, 0
    cursor.execute("LOCK TABLE central_news_article_revisions IN SHARE ROW EXCLUSIVE MODE")
    for raw in value.get("items", []):
        identity, document, targets, processing_excluded = _source_item(raw)
        document.update({"identity": identity, "stock_code": "GLOBAL"})
        content_hash = _news_article_content_hash(document)
        cursor.execute(
            "SELECT article_revision_id,content_hash FROM central_news_source_observations "
            "WHERE source_id=%s AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1",
            (source_id, identity),
        )
        source_latest = cursor.fetchone()
        cursor.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code='GLOBAL' AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1 FOR UPDATE", (identity,),
        )
        latest = cursor.fetchone()
        reusable = source_latest if source_latest and str(source_latest[1]) == content_hash else (
            latest if latest and str(latest[1]) == content_hash else None
        )
        if reusable:
            article_revision_id, duplicate = str(reusable[0]), True
            duplicate_count += 1
        else:
            article_revision_id, duplicate = uuid.uuid4().hex, False
            available_at = time()
            cursor.execute(
                "INSERT INTO central_news_article_revisions(article_revision_id,stock_code,identity,content_hash,"
                "collector_id,published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (article_revision_id, "GLOBAL", identity, content_hash, "naver", document.get("published_at"),
                 now, available_at, str(value.get("scope") or "query_set"), str(latest[0]) if latest else None,
                 json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
            )
            new_count += 1
        if not processing_excluded:
            _insert_postgres_news_job(cursor, article_revision_id, "GLOBAL", "GLOBAL", "BODY", content_hash,
                                      ARTICLE_BODY_EXTRACTOR_VERSION, document, time())
        cursor.execute(
            "SELECT article_revision_id FROM central_news_article_revisions "
            "WHERE article_revision_id=%s FOR UPDATE", (article_revision_id,),
        )
        if cursor.fetchone() is None:
            raise ValueError("소스 관측에 연결할 기사 리비전이 없습니다.")
        for target in targets:
            _insert_postgres_article_target(cursor, article_revision_id, identity, target)
            if not processing_excluded and str(target.get("relation_status") or "") == "confirmed":
                _enqueue_postgres_existing_body_rule(cursor, article_revision_id, target)
        cursor.execute(
            "INSERT INTO central_news_source_observations(observation_id,run_id,source_id,query_text,page_start,"
            "article_revision_id,identity,published_at,received_at,available_at,content_hash,duplicate,document_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (uuid.uuid4().hex, run_id, source_id, query_text, page_start, article_revision_id, identity,
             document.get("published_at"), now, time(), content_hash, duplicate,
             json.dumps({"query_membership": query_text, "targets": targets,
                         "processing_excluded": processing_excluded, **document},
                        ensure_ascii=False, separators=(",", ":"))),
        )
    raw_count = len(value.get("items", []))
    completed = float(value.get("completed_at") or time())
    cursor.execute(
        "INSERT INTO central_news_source_runs(run_revision_id,run_id,source_id,scope,page_start,checked_at,completed_at,"
        "raw_count,unique_count,duplicate_count,request_count,budget_remaining,truncated,coverage,error,document_json) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (uuid.uuid4().hex, run_id, source_id, str(value.get("scope") or "query_set"), page_start,
         float(value.get("checked_at") or now), completed, raw_count, new_count, duplicate_count,
         int(value.get("request_count") or 0), int(value.get("budget_remaining") or 0), bool(value.get("truncated")),
         str(value.get("coverage") or "query_set"), str(value.get("error") or "")[:1000],
         json.dumps(dict(value.get("document") or {}), ensure_ascii=False, separators=(",", ":"))),
    )
    _upsert_postgres_source_cursor(cursor, value, now)
    return {"raw_count": raw_count, "unique_count": new_count, "duplicate_count": duplicate_count}


def _insert_postgres_article_target(cursor: Any, article_revision_id: str, identity: str,
                                    target: dict[str, Any]) -> bool:
    code, name = str(target.get("stock_code") or "") or None, str(target.get("stock_name") or "") or None
    status, version = str(target.get("relation_status") or "unresolved"), str(target.get("rule_version") or "exact-krx-company-name-v1")
    cursor.execute(
        "SELECT target_revision_id FROM central_news_article_target_revisions WHERE article_revision_id=%s "
        "AND COALESCE(stock_code,'')=%s AND COALESCE(stock_name,'')=%s AND relation_status=%s AND rule_version=%s",
        (article_revision_id, code or "", name or "", status, version),
    )
    if cursor.fetchone():
        return False
    cursor.execute(
        "SELECT target_revision_id FROM central_news_article_target_revisions WHERE identity=%s "
        "AND COALESCE(stock_code,'')=%s ORDER BY accepted_sequence DESC LIMIT 1", (identity, code or ""),
    )
    previous = cursor.fetchone()
    cursor.execute(
        "INSERT INTO central_news_article_target_revisions(target_revision_id,article_revision_id,identity,stock_code,"
        "stock_name,relation_status,evidence_text,rule_version,available_at,revision_of,document_json) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (uuid.uuid4().hex, article_revision_id, identity, code, name, status,
         str(target.get("evidence_text") or ""), version, time(), str(previous[0]) if previous else None,
         json.dumps(dict(target), ensure_ascii=False, separators=(",", ":"))),
    )
    return True


def _enqueue_postgres_existing_body_rule(
    cursor: Any, article_revision_id: str, target: dict[str, Any],
) -> None:
    cursor.execute(
        "SELECT body_revision_id,content_hash FROM central_news_body_revisions "
        "WHERE article_revision_id=%s AND status IN ('fulltext','summary_only') "
        "ORDER BY accepted_sequence DESC LIMIT 1", (article_revision_id,),
    )
    body = cursor.fetchone()
    target_code = str(target.get("stock_code") or "")
    if body is None or not target_code:
        return
    _insert_postgres_news_job(
        cursor, article_revision_id, "GLOBAL", target_code, "RULE", str(body[1]),
        SUPPLY_CONTRACT_RULE_VERSION,
        {"body_revision_id": str(body[0]), "stock_code": target_code,
         "stock_name": str(target.get("stock_name") or target_code)},
        time(), str(body[0]),
    )


def _upsert_postgres_source_cursor(cursor: Any, value: dict[str, Any], now: float) -> None:
    cursor.execute(
        "INSERT INTO central_news_source_cursors(source_id,scope,query_text,cursor_published_at,cursor_identity,"
        "pending_published_at,pending_identity,next_start,next_schedule_at,checked_at,last_success,coverage,truncated,error,updated_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source_id) DO UPDATE SET "
        "scope=EXCLUDED.scope,query_text=EXCLUDED.query_text,cursor_published_at=EXCLUDED.cursor_published_at,"
        "cursor_identity=EXCLUDED.cursor_identity,pending_published_at=EXCLUDED.pending_published_at,"
        "pending_identity=EXCLUDED.pending_identity,next_start=EXCLUDED.next_start,next_schedule_at=EXCLUDED.next_schedule_at,"
        "checked_at=EXCLUDED.checked_at,last_success=EXCLUDED.last_success,coverage=EXCLUDED.coverage,"
        "truncated=EXCLUDED.truncated,error=EXCLUDED.error,updated_at=EXCLUDED.updated_at",
        (str(value["source_id"]), str(value.get("scope") or "query_set"), str(value["query_text"]),
         value.get("cursor_published_at"), str(value.get("cursor_identity") or ""), value.get("pending_published_at"),
         str(value.get("pending_identity") or ""), int(value.get("next_start") or 1),
         float(value.get("next_schedule_at") or now), float(value.get("checked_at") or now), value.get("last_success"),
         str(value.get("coverage") or "query_set"), bool(value.get("truncated")),
         str(value.get("error") or "")[:1000], now),
    )


def _source_diagnostics(cursor_rows: list[tuple[object, ...]], run_rows: list[tuple[object, ...]],
                        observation_rows: list[tuple[object, ...]], budget_rows: list[tuple[object, ...]],
                        job_rows: list[tuple[object, ...]], extra: dict[str, Any],
                        summary_counts: dict[str, Any],
                        source_counts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cursors = [_news_source_cursor(row) for row in cursor_rows]
    run_keys = ("run_revision_id", "run_id", "source_id", "scope", "page_start", "checked_at",
                "completed_at", "raw_count", "unique_count", "duplicate_count", "request_count",
                "budget_remaining", "truncated", "coverage", "error")
    runs = []
    for row in run_rows:
        document = row[15] if isinstance(row[15], dict) else json.loads(str(row[15]))
        runs.append({**dict(zip(run_keys, row[:15], strict=True)), "truncated": bool(row[12]), "document": document})
    observation_keys = ("observation_id", "run_id", "source_id", "query_text", "page_start",
                        "article_revision_id", "identity", "published_at", "received_at", "available_at",
                        "content_hash", "duplicate")
    observations = [{**dict(zip(observation_keys, row[:12], strict=True)), "duplicate": bool(row[11]),
                     "document": row[12] if isinstance(row[12], dict) else json.loads(str(row[12]))}
                    for row in observation_rows]
    for source in cursors:
        totals = source_counts.get(str(source["source_id"]))
        if totals:
            source.update(totals)
            source["items"] = totals["raw_count"]
        latest = next((run for run in runs if run["source_id"] == source["source_id"]), None)
        if latest:
            source["budget_remaining"] = latest["budget_remaining"]
    raw = int(summary_counts.get("raw_count") or 0)
    duplicates = int(summary_counts.get("duplicate_count") or 0)
    return {
        "scope": "query_set", "coverage": "configured_query_set", "sources": cursors,
        "summary": {**summary_counts,
                    "duplicate_rate": (duplicates / raw if raw else 0.0),
                    "budget": {str(row[0]): int(row[1]) for row in budget_rows},
                    "budget_scope": "today_kst_all_news_sources",
                    "job_queue": {str(row[0]): int(row[1]) for row in job_rows},
                    "job_queue_scope": "all_news_jobs", **extra},
        "runs": runs, "observations": observations,
    }


def _load_sqlite_news_source_diagnostics(connection: sqlite3.Connection, source_id: str,
                                         days: int, limit: int) -> dict[str, Any]:
    cutoff, bounded = time() - max(1, min(31, int(days))) * 86400, bounded_limit(limit, 1000)
    where, params = (" WHERE source_id=?", [source_id]) if source_id else ("", [])
    cursors = connection.execute("SELECT " + ",".join(_NEWS_SOURCE_CURSOR_KEYS) +
                                 " FROM central_news_source_cursors" + where + " ORDER BY source_id", params).fetchall()
    run_where = " WHERE checked_at>=?" + (" AND source_id=?" if source_id else "")
    run_params: list[object] = [cutoff] + ([source_id] if source_id else [])
    runs = connection.execute(
        "SELECT run_revision_id,run_id,source_id,scope,page_start,checked_at,completed_at,raw_count,unique_count,"
        "duplicate_count,request_count,budget_remaining,truncated,coverage,error,document_json FROM central_news_source_runs" +
        run_where + " ORDER BY accepted_sequence DESC LIMIT ?", (*run_params, bounded)).fetchall()
    observations = connection.execute(
        "SELECT observation_id,run_id,source_id,query_text,page_start,article_revision_id,identity,published_at,"
        "received_at,available_at,content_hash,duplicate,document_json FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at") + " ORDER BY accepted_sequence DESC LIMIT ?",
        (*run_params, bounded),
    ).fetchall()
    aggregate = connection.execute(
        "SELECT COALESCE(SUM(raw_count),0),COALESCE(SUM(unique_count),0),"
        "COALESCE(SUM(duplicate_count),0),COALESCE(SUM(request_count),0),"
        "COALESCE(SUM(truncated),0),COALESCE(SUM(CASE WHEN error<>'' THEN 1 ELSE 0 END),0),"
        "COALESCE(MAX(CAST(json_extract(document_json,'$.gap_seconds') AS REAL)),0) "
        "FROM central_news_source_runs" + run_where, run_params,
    ).fetchone()
    distinct_identity = connection.execute(
        "SELECT COUNT(DISTINCT identity) FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at"), run_params,
    ).fetchone()
    source_aggregates = connection.execute(
        "SELECT source_id,COALESCE(SUM(raw_count),0),COALESCE(SUM(unique_count),0),"
        "COALESCE(SUM(duplicate_count),0),COALESCE(SUM(request_count),0),"
        "COALESCE(SUM(truncated),0),COALESCE(SUM(CASE WHEN error<>'' THEN 1 ELSE 0 END),0) "
        "FROM central_news_source_runs" + run_where + " GROUP BY source_id", run_params,
    ).fetchall()
    source_identities = connection.execute(
        "SELECT source_id,COUNT(DISTINCT identity) FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at") + " GROUP BY source_id", run_params,
    ).fetchall()
    budgets = connection.execute(
        "SELECT scope,request_count FROM central_news_request_budget WHERE budget_date=?",
        (datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),),
    ).fetchall()
    jobs = connection.execute("SELECT state,COUNT(*) FROM central_news_jobs GROUP BY state").fetchall()
    observed_articles = "SELECT DISTINCT article_revision_id FROM central_news_source_observations" + run_where.replace("checked_at", "available_at")
    body = connection.execute(
        "SELECT b.status,COUNT(*) FROM central_news_body_revisions b WHERE b.article_revision_id IN "
        f"({observed_articles}) GROUP BY b.status", run_params,
    ).fetchall()
    rules = connection.execute(
        "SELECT e.event_type,COUNT(*) FROM central_news_event_revisions e WHERE e.article_revision_id IN "
        f"({observed_articles}) GROUP BY e.event_type", run_params,
    ).fetchall()
    targets = connection.execute(
        "SELECT relation_status,COUNT(*) FROM central_news_article_target_revisions WHERE article_revision_id IN "
        f"({observed_articles}) GROUP BY relation_status", run_params,
    ).fetchall()
    extra = {"body_status": {str(row[0]): int(row[1]) for row in body},
             "rule_results": {str(row[0]): int(row[1]) for row in rules},
             "target_status": {str(row[0]): int(row[1]) for row in targets}}
    summary_counts = {
        "raw_count": int(aggregate[0]), "unique_count": int(aggregate[1]),
        "duplicate_count": int(aggregate[2]), "request_count": int(aggregate[3]),
        "truncation_count": int(aggregate[4]), "error_count": int(aggregate[5]),
        "max_gap_seconds": float(aggregate[6] or 0),
        "distinct_identity_count": int(distinct_identity[0]),
    }
    identity_by_source = {str(row[0]): int(row[1]) for row in source_identities}
    source_counts = {
        str(row[0]): {"raw_count": int(row[1]), "unique_count": int(row[2]),
                      "duplicate_count": int(row[3]), "request_count": int(row[4]),
                      "truncation_count": int(row[5]), "error_count": int(row[6]),
                      "distinct_identity_count": identity_by_source.get(str(row[0]), 0)}
        for row in source_aggregates
    }
    return _source_diagnostics(
        cursors, runs, observations, budgets, jobs, extra, summary_counts, source_counts,
    )


def _load_postgres_news_source_diagnostics(cursor: Any, source_id: str,
                                           days: int, limit: int) -> dict[str, Any]:
    cutoff, bounded = time() - max(1, min(31, int(days))) * 86400, bounded_limit(limit, 1000)
    where, params = (" WHERE source_id=%s", [source_id]) if source_id else ("", [])
    cursor.execute("SELECT " + ",".join(_NEWS_SOURCE_CURSOR_KEYS) +
                   " FROM central_news_source_cursors" + where + " ORDER BY source_id", params)
    cursors = cursor.fetchall()
    run_where = " WHERE checked_at>=%s" + (" AND source_id=%s" if source_id else "")
    run_params: list[object] = [cutoff] + ([source_id] if source_id else [])
    cursor.execute(
        "SELECT run_revision_id,run_id,source_id,scope,page_start,checked_at,completed_at,raw_count,unique_count,"
        "duplicate_count,request_count,budget_remaining,truncated,coverage,error,document_json FROM central_news_source_runs" +
        run_where + " ORDER BY accepted_sequence DESC LIMIT %s", (*run_params, bounded))
    runs = cursor.fetchall()
    cursor.execute(
        "SELECT observation_id,run_id,source_id,query_text,page_start,article_revision_id,identity,published_at,"
        "received_at,available_at,content_hash,duplicate,document_json FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at") + " ORDER BY accepted_sequence DESC LIMIT %s",
        (*run_params, bounded))
    observations = cursor.fetchall()
    cursor.execute(
        "SELECT COALESCE(SUM(raw_count),0),COALESCE(SUM(unique_count),0),"
        "COALESCE(SUM(duplicate_count),0),COALESCE(SUM(request_count),0),"
        "COALESCE(SUM(CASE WHEN truncated THEN 1 ELSE 0 END),0),"
        "COALESCE(SUM(CASE WHEN error<>'' THEN 1 ELSE 0 END),0),"
        "COALESCE(MAX(COALESCE(NULLIF(document_json->>'gap_seconds','')::DOUBLE PRECISION,0)),0) "
        "FROM central_news_source_runs" + run_where, run_params)
    aggregate = cursor.fetchone()
    cursor.execute(
        "SELECT COUNT(DISTINCT identity) FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at"), run_params)
    distinct_identity = cursor.fetchone()
    cursor.execute(
        "SELECT source_id,COALESCE(SUM(raw_count),0),COALESCE(SUM(unique_count),0),"
        "COALESCE(SUM(duplicate_count),0),COALESCE(SUM(request_count),0),"
        "COALESCE(SUM(CASE WHEN truncated THEN 1 ELSE 0 END),0),"
        "COALESCE(SUM(CASE WHEN error<>'' THEN 1 ELSE 0 END),0) "
        "FROM central_news_source_runs" + run_where + " GROUP BY source_id", run_params)
    source_aggregates = cursor.fetchall()
    cursor.execute(
        "SELECT source_id,COUNT(DISTINCT identity) FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at") + " GROUP BY source_id", run_params)
    source_identities = cursor.fetchall()
    cursor.execute("SELECT scope,request_count FROM central_news_request_budget WHERE budget_date=%s",
                   (datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),))
    budgets = cursor.fetchall()
    cursor.execute("SELECT state,COUNT(*) FROM central_news_jobs GROUP BY state")
    jobs = cursor.fetchall()
    observed_articles = "SELECT DISTINCT article_revision_id FROM central_news_source_observations" + run_where.replace("checked_at", "available_at")
    cursor.execute(
        "SELECT b.status,COUNT(*) FROM central_news_body_revisions b WHERE b.article_revision_id IN "
        f"({observed_articles}) GROUP BY b.status", run_params)
    body = cursor.fetchall()
    cursor.execute(
        "SELECT e.event_type,COUNT(*) FROM central_news_event_revisions e WHERE e.article_revision_id IN "
        f"({observed_articles}) GROUP BY e.event_type", run_params)
    rules = cursor.fetchall()
    cursor.execute(
        "SELECT relation_status,COUNT(*) FROM central_news_article_target_revisions WHERE article_revision_id IN "
        f"({observed_articles}) GROUP BY relation_status", run_params)
    targets = cursor.fetchall()
    extra = {"body_status": {str(row[0]): int(row[1]) for row in body},
             "rule_results": {str(row[0]): int(row[1]) for row in rules},
             "target_status": {str(row[0]): int(row[1]) for row in targets}}
    summary_counts = {
        "raw_count": int(aggregate[0]), "unique_count": int(aggregate[1]),
        "duplicate_count": int(aggregate[2]), "request_count": int(aggregate[3]),
        "truncation_count": int(aggregate[4]), "error_count": int(aggregate[5]),
        "max_gap_seconds": float(aggregate[6] or 0),
        "distinct_identity_count": int(distinct_identity[0]),
    }
    identity_by_source = {str(row[0]): int(row[1]) for row in source_identities}
    source_counts = {
        str(row[0]): {"raw_count": int(row[1]), "unique_count": int(row[2]),
                      "duplicate_count": int(row[3]), "request_count": int(row[4]),
                      "truncation_count": int(row[5]), "error_count": int(row[6]),
                      "distinct_identity_count": identity_by_source.get(str(row[0]), 0)}
        for row in source_aggregates
    }
    return _source_diagnostics(
        cursors, runs, observations, budgets, jobs, extra, summary_counts, source_counts,
    )


def _market_news_source_prefix(source: str) -> str:
    if source == "common":
        return "naver-query:%"
    if source in {"flash", "world"}:
        return f"naver-stock:{source}:%"
    raise ValueError("unsupported market news source")


def _market_news_feed_sql(placeholder: str) -> str:
    return (
        "WITH ranked AS (SELECT o.document_json,o.article_revision_id,o.identity,o.published_at,"
        "ROW_NUMBER() OVER(PARTITION BY o.identity ORDER BY o.accepted_sequence DESC) AS rank "
        "FROM central_news_source_observations o WHERE o.source_id LIKE " + placeholder + ") "
        "SELECT ranked.document_json,d.document_json FROM ranked "
        "LEFT JOIN central_documents d ON d.collection='news_assessment' "
        "AND d.owner='GLOBAL' AND d.document_key=ranked.article_revision_id "
        "WHERE ranked.rank=1 ORDER BY ranked.published_at DESC,ranked.identity LIMIT " + placeholder
    )


def _decode_market_news_feed(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw, assessment_raw in rows:
        document = raw if isinstance(raw, dict) else json.loads(str(raw))
        if not isinstance(document, dict):
            continue
        item = {key: document.get(key) for key in (
            "title", "description", "link", "original_link", "published_at", "query_membership",
        )}
        if assessment_raw is not None:
            assessment = assessment_raw if isinstance(assessment_raw, dict) else json.loads(str(assessment_raw))
            if isinstance(assessment, dict):
                item["core_sentences"] = assessment.get("core_sentences") or []
        result.append(item)
    return result
