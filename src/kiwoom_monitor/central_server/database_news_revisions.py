"""News body, analysis, event and article revision persistence.

Store methods retain their native connection boundaries. Shared SQL helpers borrow
the caller's cursor so external job and source-page commits remain atomic.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from time import monotonic, time
from typing import Any

from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION, NEWS_ANALYSIS_SCHEMA_VERSION,
    SUPPLY_CONTRACT_RULE_VERSION, stable_document_hash,
)
from .database_codec import bounded_limit, document_value_rows, json_mapping
from .database_news_job_writes import (
    _insert_sqlite_news_job, _insert_postgres_news_job, _notify_news_job_wakeup,
)


class SQLiteNewsRevisionStoreMixin:
    def save_news_body_revision(self, value: dict[str, Any]) -> str:
        with self._lock, self._connection() as connection:
            revision_id = _save_sqlite_news_body(connection, value)
        _notify_news_job_wakeup(self)
        return revision_id

    def save_news_ai_results(self, documents: list[dict[str, Any]],
                             revisions: list[dict[str, Any]],
                             usage_documents: list[dict[str, Any]] | None = None) -> None:
        now = time()
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=excluded.updated_at,document_json=excluded.document_json "
                "WHERE central_documents.document_json<>excluded.document_json",
                document_value_rows("news_ai", documents, now),
            )
            _save_sqlite_news_ai(connection, revisions)
            if usage_documents:
                connection.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                    "updated_at=excluded.updated_at,document_json=excluded.document_json "
                    "WHERE central_documents.document_json<>excluded.document_json",
                    document_value_rows("news_request_usage", usage_documents, now),
                )

    def load_news_history(self, kind: str, *, target: str = "", identity: str = "",
                          available_at: float | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _load_sqlite_news_history(
                connection, kind, target, identity, available_at, limit,
            )

    def load_latest_news_body(self, article_revision_id: str) -> dict[str, Any] | None:
        values = self.load_news_history("body", target=article_revision_id, limit=1)
        return values[0] if values else None

    def load_news_body_revision(self, body_revision_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT body_revision_id,article_revision_id,content_hash,extractor_version,fetched_at,"
                "available_at,status,body_text,error FROM central_news_body_revisions "
                "WHERE body_revision_id=?", (body_revision_id,),
            ).fetchone()
        return _decode_news_history("body", [row])[0] if row else None

    def load_news_article_revision(self, article_revision_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
                "received_at,available_at,collection_scope,revision_of,document_json "
                "FROM central_news_article_revisions WHERE article_revision_id=?", (article_revision_id,),
            ).fetchone()
            return _decode_news_history("article", [row])[0] if row else None

    def load_stock_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _load_sqlite_stock_news_articles(connection, stock_code, limit)

    def load_confirmed_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _load_sqlite_confirmed_news_articles(connection, stock_code, limit)

    def save_news_event_revision(self, value: dict[str, Any]) -> str:
        with self._lock, self._connection() as connection:
            return _save_sqlite_news_event(connection, value)

    def find_news_ai_revision(
        self, *, target_id: str, article_revision_id: str, body_revision_id: str,
        provider: str, model: str, prompt_version: str, schema_version: str,
        input_hash: str,
    ) -> str | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT analysis_revision_id FROM central_news_ai_revisions WHERE target_id=? "
                "AND article_revision_id=? AND body_revision_id=? AND provider=? AND model=? "
                "AND prompt_version=? AND schema_version=? AND input_hash=? "
                "ORDER BY accepted_sequence DESC LIMIT 1",
                (target_id, article_revision_id, body_revision_id, provider, model,
                 prompt_version, schema_version, input_hash),
            ).fetchone()
        return str(row[0]) if row else None


class PostgresNewsRevisionStoreMixin:
    def save_news_body_revision(self, value: dict[str, Any]) -> str:
        from .postgres_access import DBWriterContext, open_observed_connection

        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="news.body", writer_kind="news_body",
            operation="save_news_body_revision", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            revision_id = _save_postgres_news_body(cursor, value)
        _notify_news_job_wakeup(self)
        from .diagnostic_metrics import record_writer_transaction
        record_writer_transaction("news_body", 1,
                                  round((monotonic() - started_at) * 1000),
                                  db_call_id=writer.call_id)
        return revision_id

    def save_news_ai_results(self, documents: list[dict[str, Any]],
                             revisions: list[dict[str, Any]],
                             usage_documents: list[dict[str, Any]] | None = None) -> None:
        now = time()
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="news.ai_results", writer_kind="news_ai_results",
            operation="save_news_ai_results",
            rows_attempted=len(documents) + len(revisions) + len(usage_documents or ()),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json "
                "WHERE central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json",
                document_value_rows("news_ai", documents, now),
            )
            _save_postgres_news_ai(cursor, revisions)
            if usage_documents:
                cursor.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                    "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json "
                    "WHERE central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json",
                    document_value_rows("news_request_usage", usage_documents, now),
                )

    def load_news_history(self, kind: str, *, target: str = "", identity: str = "",
                          available_at: float | None = None, limit: int = 100) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.news_history",
            writer_kind=f"history:{kind}",
            operation="load_news_history",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            return _load_postgres_news_history(
                cursor, kind, target, identity, available_at, limit,
            )

    def load_latest_news_body(self, article_revision_id: str) -> dict[str, Any] | None:
        values = self.load_news_history("body", target=article_revision_id, limit=1)
        return values[0] if values else None

    def load_news_body_revision(self, body_revision_id: str) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.news_revision",
            writer_kind="body_revision",
            operation="load_news_body_revision",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT body_revision_id,article_revision_id,content_hash,extractor_version,fetched_at,"
                "available_at,status,body_text,error FROM central_news_body_revisions "
                "WHERE body_revision_id=%s", (body_revision_id,),
            )
            row = cursor.fetchone()
        return _decode_news_history("body", [row])[0] if row else None

    def load_news_article_revision(self, article_revision_id: str) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.news_revision",
            writer_kind="article_revision",
            operation="load_news_article_revision",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
                "received_at,available_at,collection_scope,revision_of,document_json "
                "FROM central_news_article_revisions WHERE article_revision_id=%s", (article_revision_id,),
            )
            row = cursor.fetchone()
            return _decode_news_history("article", [row])[0] if row else None

    def load_stock_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.news_publications",
            writer_kind="stock_articles",
            operation="load_stock_news_articles",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            return _load_postgres_stock_news_articles(cursor, stock_code, limit)

    def load_confirmed_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.news_publications",
            writer_kind="confirmed_articles",
            operation="load_confirmed_news_articles",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            return _load_postgres_confirmed_news_articles(cursor, stock_code, limit)

    def save_news_event_revision(self, value: dict[str, Any]) -> str:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="news.event", writer_kind="news_event",
            operation="save_news_event_revision", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            return _save_postgres_news_event(cursor, value)

    def find_news_ai_revision(
        self, *, target_id: str, article_revision_id: str, body_revision_id: str,
        provider: str, model: str, prompt_version: str, schema_version: str,
        input_hash: str,
    ) -> str | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.news_ai_revisions", writer_kind="analysis_revision",
            operation="find_news_ai_revision", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT analysis_revision_id FROM central_news_ai_revisions WHERE target_id=%s "
                "AND article_revision_id=%s AND body_revision_id=%s AND provider=%s AND model=%s "
                "AND prompt_version=%s AND schema_version=%s AND input_hash=%s "
                "ORDER BY accepted_sequence DESC LIMIT 1",
                (target_id, article_revision_id, body_revision_id, provider, model,
                 prompt_version, schema_version, input_hash),
            )
            row = cursor.fetchone()
        return str(row[0]) if row else None


def _save_sqlite_news_body(connection: sqlite3.Connection, value: dict[str, Any]) -> str:
    revision_id = uuid.uuid4().hex
    body = str(value.get("body_text") or "")
    content_hash = stable_document_hash({"body": body})
    article_revision_id = str(value["article_revision_id"])
    connection.execute(
        "INSERT INTO central_news_body_revisions(body_revision_id,article_revision_id,content_hash,"
        "extractor_version,fetched_at,available_at,status,body_text,error) VALUES(?,?,?,?,?,?,?,?,?)",
        (revision_id, article_revision_id, content_hash,
         str(value.get("extractor_version") or ARTICLE_BODY_EXTRACTOR_VERSION),
         float(value.get("fetched_at") or time()), time(), str(value["status"]), body,
         str(value.get("error") or "")[:1000]),
    )
    article = connection.execute(
        "SELECT stock_code,document_json,collection_scope FROM central_news_article_revisions WHERE article_revision_id=?",
        (article_revision_id,),
    ).fetchone()
    if article is None:
        raise ValueError("규칙 작업을 예약할 기사 리비전이 없습니다.")
    stock = str(article[0])
    document = article[1] if isinstance(article[1], dict) else json.loads(str(article[1]))
    targets = [(stock, str(document.get("stock_name") or stock))]
    if stock == "GLOBAL":
        targets = [(str(row[0]), str(row[1])) for row in connection.execute(
            "SELECT stock_code,stock_name FROM central_news_article_target_revisions "
            "WHERE article_revision_id=? AND relation_status='confirmed' ORDER BY accepted_sequence", (article_revision_id,),
        ).fetchall()]
        if str(article[2]) in {"naver_stock_market", "historical_market_backfill",
                               "historical_market_pc_backfill"}:
            targets.insert(0, ("GLOBAL", "시황"))
    for target_code, target_name in targets:
        _insert_sqlite_news_job(
            connection, article_revision_id, stock, target_code, "RULE", content_hash,
            SUPPLY_CONTRACT_RULE_VERSION,
            {"body_revision_id": revision_id, "stock_code": target_code, "stock_name": target_name},
            time(), revision_id,
        )
    return revision_id


def _save_postgres_news_body(cursor: Any, value: dict[str, Any]) -> str:
    revision_id = uuid.uuid4().hex
    body = str(value.get("body_text") or "")
    content_hash = stable_document_hash({"body": body})
    article_revision_id = str(value["article_revision_id"])
    cursor.execute(
        "INSERT INTO central_news_body_revisions(body_revision_id,article_revision_id,content_hash,"
        "extractor_version,fetched_at,available_at,status,body_text,error) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (revision_id, article_revision_id, content_hash,
         str(value.get("extractor_version") or ARTICLE_BODY_EXTRACTOR_VERSION),
         float(value.get("fetched_at") or time()), time(), str(value["status"]), body,
         str(value.get("error") or "")[:1000]),
    )
    cursor.execute(
        "SELECT stock_code,document_json,collection_scope FROM central_news_article_revisions "
        "WHERE article_revision_id=%s FOR UPDATE",
        (article_revision_id,),
    )
    article = cursor.fetchone()
    if article is None:
        raise ValueError("규칙 작업을 예약할 기사 리비전이 없습니다.")
    stock = str(article[0])
    document = article[1] if isinstance(article[1], dict) else json.loads(str(article[1]))
    targets = [(stock, str(document.get("stock_name") or stock))]
    if stock == "GLOBAL":
        cursor.execute(
            "SELECT stock_code,stock_name FROM central_news_article_target_revisions "
            "WHERE article_revision_id=%s AND relation_status='confirmed' ORDER BY accepted_sequence",
            (article_revision_id,),
        )
        targets = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
        if str(article[2]) in {"naver_stock_market", "historical_market_backfill",
                               "historical_market_pc_backfill"}:
            targets.insert(0, ("GLOBAL", "시황"))
    for target_code, target_name in targets:
        _insert_postgres_news_job(
            cursor, article_revision_id, stock, target_code, "RULE", content_hash,
            SUPPLY_CONTRACT_RULE_VERSION,
            {"body_revision_id": revision_id, "stock_code": target_code, "stock_name": target_name},
            time(), revision_id,
        )
    return revision_id


def _save_sqlite_news_ai(connection: sqlite3.Connection, values: list[dict[str, Any]]) -> None:
    for value in values:
        connection.execute(
            "INSERT INTO central_news_ai_revisions(analysis_revision_id,target_id,article_revision_id,"
            "body_revision_id,provider,model,prompt_version,schema_version,input_hash,computed_at,"
            "available_at,output_json,usage_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(value.get("analysis_revision_id") or uuid.uuid4().hex), str(value["target_id"]), str(value["article_revision_id"]),
             value.get("body_revision_id"), str(value["provider"]), str(value["model"]),
             str(value["prompt_version"]), str(value.get("schema_version") or NEWS_ANALYSIS_SCHEMA_VERSION),
             str(value["input_hash"]), float(value.get("computed_at") or time()), time(),
             json.dumps(value["output"], ensure_ascii=False, separators=(",", ":")),
             json.dumps(value.get("usage", {}), ensure_ascii=False, separators=(",", ":"))),
        )


def _save_postgres_news_ai(cursor: Any, values: list[dict[str, Any]]) -> None:
    for value in values:
        cursor.execute(
            "INSERT INTO central_news_ai_revisions(analysis_revision_id,target_id,article_revision_id,"
            "body_revision_id,provider,model,prompt_version,schema_version,input_hash,computed_at,"
            "available_at,output_json,usage_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(value.get("analysis_revision_id") or uuid.uuid4().hex), str(value["target_id"]), str(value["article_revision_id"]),
             value.get("body_revision_id"), str(value["provider"]), str(value["model"]),
             str(value["prompt_version"]), str(value.get("schema_version") or NEWS_ANALYSIS_SCHEMA_VERSION),
             str(value["input_hash"]), float(value.get("computed_at") or time()), time(),
             json.dumps(value["output"], ensure_ascii=False, separators=(",", ":")),
             json.dumps(value.get("usage", {}), ensure_ascii=False, separators=(",", ":"))),
        )


def _save_sqlite_news_event(connection: sqlite3.Connection, value: dict[str, Any]) -> str:
    article_revision_id = str(value["article_revision_id"])
    body_revision_id = str(value["body_revision_id"])
    rule_version = str(value["rule_version"])
    input_hash = str(value["input_hash"])
    target_stock = str(value.get("stock_code") or "")
    existing = connection.execute(
        "SELECT event_revision_id FROM central_news_event_revisions WHERE article_revision_id=? "
        "AND body_revision_id=? AND rule_version=? AND input_hash=? AND (?='' OR stock_code=?)",
        (article_revision_id, body_revision_id, rule_version, input_hash, target_stock, target_stock),
    ).fetchone()
    if existing:
        return str(existing[0])
    article = connection.execute(
        "SELECT stock_code,identity FROM central_news_article_revisions WHERE article_revision_id=?",
        (article_revision_id,),
    ).fetchone()
    if article is None:
        raise ValueError("사건에 연결할 기사 리비전이 없습니다.")
    stock_code, identity = target_stock or str(article[0]), str(article[1])
    result = dict(value["result"])
    event_key = str(result.get("event_key") or "") or None
    prior = connection.execute(
        "SELECT m.event_id FROM central_news_event_membership_revisions m "
        "JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id "
        "JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id "
        "WHERE e.stock_code=? AND a.identity=? ORDER BY m.accepted_sequence DESC LIMIT 1",
        (stock_code, identity),
    ).fetchone()
    same_identity = prior is not None
    if prior is None and event_key:
        prior = connection.execute(
            "SELECT event_id FROM central_news_event_revisions WHERE stock_code=? AND event_key=? "
            "ORDER BY accepted_sequence DESC LIMIT 1", (stock_code, event_key),
        ).fetchone()
    event_id = str(prior[0]) if prior else uuid.uuid4().hex
    previous = connection.execute(
        "SELECT event_revision_id,certainty FROM central_news_event_revisions WHERE event_id=? "
        "ORDER BY accepted_sequence DESC LIMIT 1", (event_id,),
    ).fetchone()
    if previous and result.get("novelty") == "NEW":
        if not same_identity and str(previous[1]) == str(result.get("certainty")):
            result["novelty"], result["novelty_score"] = "REPUBLICATION", 10
        else:
            result["novelty"], result["novelty_score"] = "UPDATE", 60
    related = _sqlite_possible_related(connection, stock_code, event_id, value.get("candidate_identities", ()))
    result["possible_related_event_ids"] = related
    result["article_revision_id"], result["body_revision_id"] = article_revision_id, body_revision_id
    event_revision_id, available_at = uuid.uuid4().hex, time()
    connection.execute(
        "INSERT INTO central_news_event_revisions(event_revision_id,event_id,event_key,stock_code,event_type,"
        "article_revision_id,body_revision_id,rule_version,input_hash,role,scope,certainty,novelty,amount_won,"
        "counterparty,importance_score,confidence_score,novelty_score,ai_required,available_at,revision_of,result_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event_revision_id, event_id, event_key, stock_code, str(result["event_type"]), article_revision_id,
         body_revision_id, rule_version, input_hash, str(result["role"]), str(result["scope"]),
         str(result["certainty"]), str(result["novelty"]), result.get("amount_won"),
         result.get("counterparty"), int(result["importance_score"]), int(result["confidence_score"]),
         int(result["novelty_score"]), int(bool(result["ai_required"])), available_at,
         str(previous[0]) if previous else None,
         json.dumps(result, ensure_ascii=False, separators=(",", ":"))),
    )
    previous_membership = connection.execute(
        "SELECT membership_revision_id FROM central_news_event_membership_revisions WHERE event_id=? "
        "AND article_revision_id IN (SELECT article_revision_id FROM central_news_article_revisions "
        "WHERE identity=?) ORDER BY accepted_sequence DESC LIMIT 1",
        (event_id, identity),
    ).fetchone()
    membership = {"identity": identity, "relation": "EVIDENCE", "possible_related_event_ids": related}
    connection.execute(
        "INSERT INTO central_news_event_membership_revisions(membership_revision_id,event_id,event_revision_id,"
        "article_revision_id,body_revision_id,relation,available_at,revision_of,document_json) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, event_id, event_revision_id, article_revision_id, body_revision_id, "EVIDENCE",
         available_at, str(previous_membership[0]) if previous_membership else None,
         json.dumps(membership, ensure_ascii=False, separators=(",", ":"))),
    )
    return event_revision_id


def _save_postgres_news_event(cursor: Any, value: dict[str, Any]) -> str:
    cursor.execute("LOCK TABLE central_news_event_revisions IN SHARE ROW EXCLUSIVE MODE")
    article_revision_id, body_revision_id = str(value["article_revision_id"]), str(value["body_revision_id"])
    rule_version, input_hash = str(value["rule_version"]), str(value["input_hash"])
    target_stock = str(value.get("stock_code") or "")
    cursor.execute(
        "SELECT event_revision_id FROM central_news_event_revisions WHERE article_revision_id=%s "
        "AND body_revision_id=%s AND rule_version=%s AND input_hash=%s AND (%s='' OR stock_code=%s)",
        (article_revision_id, body_revision_id, rule_version, input_hash, target_stock, target_stock),
    )
    existing = cursor.fetchone()
    if existing:
        return str(existing[0])
    cursor.execute(
        "SELECT stock_code,identity FROM central_news_article_revisions WHERE article_revision_id=%s",
        (article_revision_id,),
    )
    article = cursor.fetchone()
    if article is None:
        raise ValueError("사건에 연결할 기사 리비전이 없습니다.")
    stock_code, identity = target_stock or str(article[0]), str(article[1])
    result = dict(value["result"])
    event_key = str(result.get("event_key") or "") or None
    cursor.execute(
        "SELECT m.event_id FROM central_news_event_membership_revisions m "
        "JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id "
        "JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id "
        "WHERE e.stock_code=%s AND a.identity=%s ORDER BY m.accepted_sequence DESC LIMIT 1",
        (stock_code, identity),
    )
    prior = cursor.fetchone()
    same_identity = prior is not None
    if prior is None and event_key:
        cursor.execute(
            "SELECT event_id FROM central_news_event_revisions WHERE stock_code=%s AND event_key=%s "
            "ORDER BY accepted_sequence DESC LIMIT 1", (stock_code, event_key),
        )
        prior = cursor.fetchone()
    event_id = str(prior[0]) if prior else uuid.uuid4().hex
    cursor.execute(
        "SELECT event_revision_id,certainty FROM central_news_event_revisions WHERE event_id=%s "
        "ORDER BY accepted_sequence DESC LIMIT 1", (event_id,),
    )
    previous = cursor.fetchone()
    if previous and result.get("novelty") == "NEW":
        if not same_identity and str(previous[1]) == str(result.get("certainty")):
            result["novelty"], result["novelty_score"] = "REPUBLICATION", 10
        else:
            result["novelty"], result["novelty_score"] = "UPDATE", 60
    related = _postgres_possible_related(cursor, stock_code, event_id, value.get("candidate_identities", ()))
    result["possible_related_event_ids"] = related
    result["article_revision_id"], result["body_revision_id"] = article_revision_id, body_revision_id
    event_revision_id, available_at = uuid.uuid4().hex, time()
    cursor.execute(
        "INSERT INTO central_news_event_revisions(event_revision_id,event_id,event_key,stock_code,event_type,"
        "article_revision_id,body_revision_id,rule_version,input_hash,role,scope,certainty,novelty,amount_won,"
        "counterparty,importance_score,confidence_score,novelty_score,ai_required,available_at,revision_of,result_json) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (event_revision_id, event_id, event_key, stock_code, str(result["event_type"]), article_revision_id,
         body_revision_id, rule_version, input_hash, str(result["role"]), str(result["scope"]),
         str(result["certainty"]), str(result["novelty"]), result.get("amount_won"),
         result.get("counterparty"), int(result["importance_score"]), int(result["confidence_score"]),
         int(result["novelty_score"]), bool(result["ai_required"]), available_at,
         str(previous[0]) if previous else None, json.dumps(result, ensure_ascii=False, separators=(",", ":"))),
    )
    cursor.execute(
        "SELECT membership_revision_id FROM central_news_event_membership_revisions WHERE event_id=%s "
        "AND article_revision_id IN (SELECT article_revision_id FROM central_news_article_revisions "
        "WHERE identity=%s) ORDER BY accepted_sequence DESC LIMIT 1",
        (event_id, identity),
    )
    previous_membership = cursor.fetchone()
    membership = {"identity": identity, "relation": "EVIDENCE", "possible_related_event_ids": related}
    cursor.execute(
        "INSERT INTO central_news_event_membership_revisions(membership_revision_id,event_id,event_revision_id,"
        "article_revision_id,body_revision_id,relation,available_at,revision_of,document_json) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (uuid.uuid4().hex, event_id, event_revision_id, article_revision_id, body_revision_id, "EVIDENCE",
         available_at, str(previous_membership[0]) if previous_membership else None,
         json.dumps(membership, ensure_ascii=False, separators=(",", ":"))),
    )
    return event_revision_id


def _sqlite_possible_related(connection: sqlite3.Connection, stock_code: str, event_id: str,
                             identities: Any) -> list[str]:
    values = [str(value) for value in identities if str(value)]
    rows: list[tuple[object, ...]] = []
    if values:
        placeholders = ",".join("?" for _ in values)
        rows = connection.execute(
            "SELECT DISTINCT m.event_id FROM central_news_event_membership_revisions m "
            "JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id "
            "JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id "
            f"WHERE e.stock_code=? AND a.identity IN ({placeholders}) ORDER BY m.accepted_sequence DESC LIMIT 5",
            (stock_code, *values),
        ).fetchall()
    if not rows:
        rows = connection.execute(
            "SELECT event_id FROM central_news_event_revisions WHERE stock_code=? AND event_id<>? "
            "ORDER BY accepted_sequence DESC LIMIT 5", (stock_code, event_id),
        ).fetchall()
    return [str(row[0]) for row in rows if str(row[0]) != event_id]


def _postgres_possible_related(cursor: Any, stock_code: str, event_id: str, identities: Any) -> list[str]:
    values = [str(value) for value in identities if str(value)]
    if values:
        cursor.execute(
            "SELECT DISTINCT m.event_id FROM central_news_event_membership_revisions m "
            "JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id "
            "JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id "
            "WHERE e.stock_code=%s AND a.identity=ANY(%s) ORDER BY m.event_id LIMIT 5",
            (stock_code, values),
        )
        rows = cursor.fetchall()
    else:
        rows = []
    if not rows:
        cursor.execute(
            "SELECT event_id FROM central_news_event_revisions WHERE stock_code=%s AND event_id<>%s "
            "ORDER BY accepted_sequence DESC LIMIT 5", (stock_code, event_id),
        )
        rows = cursor.fetchall()
    return [str(row[0]) for row in rows if str(row[0]) != event_id]


def _news_history_query(kind: str, placeholder: str, target: str, identity: str,
                        available_at: float | None, limit: int) -> tuple[str, list[object]]:
    if kind == "article":
        sql = ("SELECT article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
               "received_at,available_at,collection_scope,revision_of,document_json "
               "FROM central_news_article_revisions WHERE 1=1")
        target_column, identity_column = "stock_code", "identity"
    elif kind == "body":
        sql = ("SELECT body_revision_id,article_revision_id,content_hash,extractor_version,fetched_at,"
               "available_at,status,body_text,error FROM central_news_body_revisions WHERE 1=1")
        target_column, identity_column = "article_revision_id", ""
    elif kind == "ai":
        sql = ("SELECT analysis_revision_id,target_id,article_revision_id,body_revision_id,provider,model,"
               "prompt_version,schema_version,input_hash,computed_at,available_at,output_json,usage_json "
               "FROM central_news_ai_revisions WHERE 1=1")
        target_column, identity_column = "target_id", ""
    elif kind == "event":
        sql = ("SELECT event_revision_id,event_id,event_key,stock_code,event_type,article_revision_id,"
               "body_revision_id,rule_version,input_hash,role,scope,certainty,novelty,amount_won,counterparty,"
               "importance_score,confidence_score,novelty_score,ai_required,available_at,revision_of,result_json "
               "FROM central_news_event_revisions WHERE 1=1")
        target_column, identity_column = "stock_code", "event_id"
    elif kind == "membership":
        sql = ("SELECT membership_revision_id,event_id,event_revision_id,article_revision_id,body_revision_id,"
               "relation,available_at,revision_of,document_json "
               "FROM central_news_event_membership_revisions WHERE 1=1")
        target_column, identity_column = "event_id", "article_revision_id"
    else:
        raise ValueError("뉴스 이력 종류는 article/body/ai/event/membership 중 하나여야 합니다.")
    parameters: list[object] = []
    if target:
        sql += f" AND {target_column}={placeholder}"
        parameters.append(target)
    if identity and identity_column:
        sql += f" AND {identity_column}={placeholder}"
        parameters.append(identity)
    if available_at is not None:
        sql += f" AND available_at<={placeholder}"
        parameters.append(float(available_at))
    sql += f" ORDER BY accepted_sequence DESC LIMIT {placeholder}"
    parameters.append(bounded_limit(limit, 1000))
    return sql, parameters


def _decode_news_history(kind: str, rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    if kind == "article":
        keys = ("article_revision_id", "stock_code", "identity", "content_hash", "collector_id",
                "published_at", "received_at", "available_at", "collection_scope", "revision_of")
        return [{**dict(zip(keys, row[:10], strict=True)),
                 "document": row[10] if isinstance(row[10], dict) else json.loads(str(row[10]))} for row in rows]
    if kind == "body":
        keys = ("body_revision_id", "article_revision_id", "content_hash", "extractor_version",
                "fetched_at", "available_at", "status", "body_text", "error")
        return [dict(zip(keys, row, strict=True)) for row in rows]
    if kind == "event":
        keys = ("event_revision_id", "event_id", "event_key", "stock_code", "event_type",
                "article_revision_id", "body_revision_id", "rule_version", "input_hash", "role", "scope",
                "certainty", "novelty", "amount_won", "counterparty", "importance_score",
                "confidence_score", "novelty_score", "ai_required", "available_at", "revision_of")
        return [{**dict(zip(keys, row[:21], strict=True)), "ai_required": bool(row[18]),
                 "result": json_mapping(row[21])} for row in rows]
    if kind == "membership":
        keys = ("membership_revision_id", "event_id", "event_revision_id", "article_revision_id",
                "body_revision_id", "relation", "available_at", "revision_of")
        return [{**dict(zip(keys, row[:8], strict=True)),
                 "document": json_mapping(row[8])} for row in rows]
    keys = ("analysis_revision_id", "target_id", "article_revision_id", "body_revision_id",
            "provider", "model", "prompt_version", "schema_version", "input_hash", "computed_at",
            "available_at")
    return [{**dict(zip(keys, row[:11], strict=True)),
             "output": row[11] if isinstance(row[11], dict) else json.loads(str(row[11])),
             "usage": row[12] if isinstance(row[12], dict) else json.loads(str(row[12]))} for row in rows]


def _load_sqlite_news_history(connection: sqlite3.Connection, kind: str, target: str,
                              identity: str, available_at: float | None, limit: int) -> list[dict[str, Any]]:
    sql, parameters = _news_history_query(kind, "?", target, identity, available_at, limit)
    return _decode_news_history(kind, connection.execute(sql, parameters).fetchall())


def _load_postgres_news_history(cursor: Any, kind: str, target: str, identity: str,
                                available_at: float | None, limit: int) -> list[dict[str, Any]]:
    sql, parameters = _news_history_query(kind, "%s", target, identity, available_at, limit)
    cursor.execute(sql, parameters)
    return _decode_news_history(kind, cursor.fetchall())


def _confirmed_news_articles_query(placeholder: str, *, postgres: bool = False) -> str:
    published_order = "ranked.published_at::timestamptz" if postgres else "julianday(ranked.published_at)"
    return (
        "WITH ranked AS ("
        "SELECT a.document_json,a.accepted_sequence,a.article_revision_id,a.published_at,a.identity,"
        "ROW_NUMBER() OVER(PARTITION BY a.identity ORDER BY a.accepted_sequence DESC) AS identity_rank "
        "FROM central_news_article_target_revisions t "
        "JOIN central_news_article_revisions a ON a.article_revision_id=t.article_revision_id "
        f"WHERE t.stock_code={placeholder} AND t.relation_status='confirmed' AND a.stock_code='GLOBAL') "
        "SELECT ranked.document_json,(SELECT b.status FROM central_news_body_revisions b "
        "WHERE b.article_revision_id=ranked.article_revision_id "
        "ORDER BY b.accepted_sequence DESC LIMIT 1),"
        "(SELECT b.body_text FROM central_news_body_revisions b "
        "WHERE b.article_revision_id=ranked.article_revision_id "
        "ORDER BY b.accepted_sequence DESC LIMIT 1),d.document_json "
        "FROM ranked LEFT JOIN central_documents d ON d.collection='news_assessment' "
        f"AND d.owner={placeholder} AND d.document_key=ranked.article_revision_id "
        "WHERE identity_rank=1 "
        f"ORDER BY {published_order} DESC NULLS LAST,ranked.identity ASC LIMIT {placeholder}"
    )


def _stock_news_articles_query(placeholder: str, *, postgres: bool = False) -> str:
    published_order = "ranked.published_at::timestamptz" if postgres else "julianday(ranked.published_at)"
    return (
        "WITH ranked AS ("
        "SELECT a.document_json,a.accepted_sequence,a.article_revision_id,a.published_at,a.identity,"
        "ROW_NUMBER() OVER(PARTITION BY a.identity ORDER BY a.accepted_sequence DESC) AS identity_rank "
        "FROM central_news_article_revisions a "
        f"WHERE a.stock_code={placeholder} AND a.collection_scope='watchlist') "
        "SELECT ranked.document_json,(SELECT b.status FROM central_news_body_revisions b "
        "WHERE b.article_revision_id=ranked.article_revision_id "
        "ORDER BY b.accepted_sequence DESC LIMIT 1),"
        "(SELECT b.body_text FROM central_news_body_revisions b "
        "WHERE b.article_revision_id=ranked.article_revision_id "
        "ORDER BY b.accepted_sequence DESC LIMIT 1),d.document_json "
        "FROM ranked LEFT JOIN central_documents d ON d.collection='news_assessment' "
        f"AND d.owner={placeholder} AND d.document_key=ranked.article_revision_id "
        "WHERE identity_rank=1 "
        f"ORDER BY {published_order} DESC NULLS LAST,ranked.identity ASC LIMIT {placeholder}"
    )


def _decode_confirmed_news_articles(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        document = row[0] if isinstance(row[0], dict) else json.loads(str(row[0]))
        if isinstance(document, dict):
            value = dict(document)
            value["_body_status"] = str(row[1] or "")
            value["_body_text"] = str(row[2] or "")
            if row[3] is not None:
                value["_assessment"] = row[3] if isinstance(row[3], dict) else json.loads(str(row[3]))
            result.append(value)
    return result


def _load_sqlite_stock_news_articles(
    connection: sqlite3.Connection, stock_code: str, limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        _stock_news_articles_query("?"),
        (stock_code, stock_code, bounded_limit(limit, 100_000)),
    ).fetchall()
    return _decode_confirmed_news_articles(rows)


def _load_postgres_stock_news_articles(
    cursor: Any, stock_code: str, limit: int,
) -> list[dict[str, Any]]:
    cursor.execute(
        _stock_news_articles_query("%s", postgres=True),
        (stock_code, stock_code, bounded_limit(limit, 100_000)),
    )
    return _decode_confirmed_news_articles(cursor.fetchall())


def _load_sqlite_confirmed_news_articles(
    connection: sqlite3.Connection, stock_code: str, limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        _confirmed_news_articles_query("?"),
        (stock_code, stock_code, bounded_limit(limit, 100_000)),
    ).fetchall()
    return _decode_confirmed_news_articles(rows)


def _load_postgres_confirmed_news_articles(
    cursor: Any, stock_code: str, limit: int,
) -> list[dict[str, Any]]:
    cursor.execute(
        _confirmed_news_articles_query("%s", postgres=True),
        (stock_code, stock_code, bounded_limit(limit, 100_000)),
    )
    return _decode_confirmed_news_articles(cursor.fetchall())
