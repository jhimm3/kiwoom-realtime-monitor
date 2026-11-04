"""News body, analysis, event and article revision persistence.

Store methods retain their native connection boundaries. Shared SQL helpers borrow
the caller's cursor so external job and source-page commits remain atomic.
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from time import monotonic, time
from typing import Any, Callable
from datetime import datetime

from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION, NEWS_ANALYSIS_SCHEMA_VERSION,
    SUPPLY_CONTRACT_RULE_VERSION, stable_document_hash,
    news_article_content_hash, news_projection_article_hash, NewsBodyConflict,
)
from .database_codec import bounded_limit, document_value_rows, json_mapping
from kiwoom_monitor.domain.news_ai_contract import (
    decode_news_ai_provenance, decode_news_input_source, native_news_source_sequence,
    news_ai_source_version_status, news_ai_shared_applies, validate_news_ai_input,
)
from .database_news_job_writes import (
    _insert_sqlite_news_job, _insert_postgres_news_job, _notify_news_job_wakeup,
    _news_job_rows, _NEWS_JOB_CLAIM_HISTORICAL_SCOPES_SQL,
)


_NEWS_AI_REVISION_COLUMNS = (
    "analysis_revision_id,target_id,article_revision_id,body_revision_id,provider,model,"
    "prompt_version,schema_version,input_hash,computed_at,available_at,output_json,usage_json"
)


def _news_ai_provenance(value: dict[str, Any]) -> dict[str, object]:
    """Project an existing immutable row; never expose inference input text."""
    return decode_news_ai_provenance({
        "version": 1, "status": "ACCEPTED", "origin": "native_accepted",
        **{name: value[name] for name in (
            "analysis_revision_id", "target_id", "article_revision_id", "body_revision_id",
            "provider", "model", "prompt_version", "schema_version", "input_hash",
            "computed_at", "available_at",
        )},
        "output_hash": stable_document_hash(value["output"]),
        "inference_ref": value["usage"].get("inference_ref"),
        "requester": value["usage"].get("requester"),
        **({"input_binding": value["usage"]["input_binding"]} if "input_binding" in value["usage"] else {}),
    })


def _news_ai_has_source_binding(reference: object) -> bool:
    """Pure admission to the existing current-BODY selection, not a write grant."""
    binding = decode_news_ai_provenance(reference).get("input_binding")
    return isinstance(binding, dict) and binding.get("status") != "UNKNOWN"


class SQLiteNewsRevisionStoreMixin:
    def claim_news_body_job(self, owner: str, *, now: float):
        with self._lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return _news_body_claim(db,owner,float(now),postgres=False)

    def load_news_body_job_input(self, owner: str, key: str, attempts: int):
        with self._lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return _news_body_input(db,owner,key,attempts,postgres=False)

    def load_news_body_job_receipt(self, owner: str, key: str, attempts: int):
        with self._lock, self._connection() as db:
            return _news_body_receipt(db,owner,key,attempts,postgres=False)

    def accept_news_body_job_result(self, owner: str, key: str, envelope: dict):
        with self._lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt,wake=_news_body_accept(db,owner,key,envelope,postgres=False,now_provider=self._now_provider)
        if wake:
            _notify_news_job_wakeup(self)
        return receipt

    def accept_news_job_result(self, job_key: str, result: dict[str, Any], *,
                               attempts: int) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt, wake = _accept_news_job_result(
                connection, job_key, result, attempts=attempts,
                postgres=False, now_provider=self._now_provider,
            )
        if wake:
            _notify_news_job_wakeup(self)
        return receipt

    def save_news_body_revision(self, value: dict[str, Any]) -> str:
        return str(SQLiteNewsRevisionStoreMixin.save_news_body_revision_receipt(self, value)["body_revision_id"])

    def save_news_body_revision_receipt(self, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            receipt = _save_sqlite_news_body(connection, value, now=self._now_provider().timestamp())
        _notify_news_job_wakeup(self)
        return receipt

    def save_news_ai_results(self, documents: list[dict[str, Any]],
                             revisions: list[dict[str, Any]],
                             usage_documents: list[dict[str, Any]] | None = None) -> list[dict[str, object]]:
        now = self._now_provider().timestamp()
        with self._lock, self._connection() as connection:
            return _save_news_ai_results(connection, documents, revisions, usage_documents, postgres=False, now=now)

    def load_news_history(self, kind: str, *, target: str = "", identity: str = "",
                          available_at: float | None = None, limit: int = 100,
                          before_revision_id: str = "") -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _load_sqlite_news_history(
                connection, kind, target, identity, available_at, limit, before_revision_id,
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
    def _news_body_command(self, operation, owner, function, *args, **kwargs):
        from .postgres_access import DBWriterContext, open_observed_connection
        context=DBWriterContext(writer_family='news.body_processor',writer_kind='news_body_processor',
            operation=operation,source=owner)
        with open_observed_connection(self._connect,context) as connection, connection.cursor() as db:
            return function(db,owner,*args,postgres=True,**kwargs)

    def claim_news_body_job(self, owner: str, *, now: float):
        return self._news_body_command('claim_news_body_job',owner,_news_body_claim,float(now))

    def load_news_body_job_input(self, owner: str, key: str, attempts: int):
        return self._news_body_command('load_news_body_job_input',owner,_news_body_input,key,attempts)

    def load_news_body_job_receipt(self, owner: str, key: str, attempts: int):
        return self._news_body_command('load_news_body_job_receipt',owner,_news_body_receipt,key,attempts)

    def accept_news_body_job_result(self, owner: str, key: str, envelope: dict):
        receipt,wake=self._news_body_command('accept_news_body_job_result',owner,_news_body_accept,key,envelope,now_provider=self._now_provider)
        if wake:
            _notify_news_job_wakeup(self)
        return receipt

    def accept_news_job_result(self, job_key: str, result: dict[str, Any], *,
                               attempts: int) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="news.job_result", writer_kind="news_job_result",
            operation="accept_news_job_result", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            if result.get("stage") == "AI":
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            receipt, wake = _accept_news_job_result(
                cursor, job_key, result, attempts=attempts,
                postgres=True, now_provider=self._now_provider,
            )
        if wake:
            _notify_news_job_wakeup(self)
        return receipt

    def save_news_body_revision(self, value: dict[str, Any]) -> str:
        return str(PostgresNewsRevisionStoreMixin.save_news_body_revision_receipt(self, value)["body_revision_id"])

    def save_news_body_revision_receipt(self, value: dict[str, Any]) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="news.body", writer_kind="news_body",
            operation="save_news_body_revision", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            receipt = _save_postgres_news_body(cursor, value, now=self._now_provider().timestamp())
        _notify_news_job_wakeup(self)
        from .postgres_access import database_observer
        observer = database_observer()
        if observer is not None:
            observer.record_writer_transaction("news_body", 1,
                                      round((monotonic() - started_at) * 1000),
                                      db_call_id=writer.call_id)
        return receipt

    def save_news_ai_results(self, documents: list[dict[str, Any]],
                             revisions: list[dict[str, Any]],
                             usage_documents: list[dict[str, Any]] | None = None) -> list[dict[str, object]]:
        now = self._now_provider().timestamp()
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="news.ai_results", writer_kind="news_ai_results",
            operation="save_news_ai_results",
            rows_attempted=(len(documents) + len(revisions) + len(usage_documents or ())
                            + sum("inference" in value for value in (usage_documents or ()))),
        )
        needs_source_cut = any(_news_ai_has_source_binding(value.get("usage")) for value in revisions) or any(
            _news_ai_has_source_binding(reference) for value in documents
            for reference in (value.get("input_provenance"), value["document"].get("provenance_json")))
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            if needs_source_cut:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            return _save_news_ai_results(cursor, documents, revisions, usage_documents, postgres=True, now=now)

    def load_news_history(self, kind: str, *, target: str = "", identity: str = "",
                          available_at: float | None = None, limit: int = 100,
                          before_revision_id: str = "") -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.news_history",
            writer_kind=f"history:{kind}",
            operation="load_news_history",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            return _load_postgres_news_history(
                cursor, kind, target, identity, available_at, limit, before_revision_id,
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


def _accept_news_job_result(db: Any, job_key: str, result: dict[str, Any], *,
                            attempts: int, postgres: bool,
                            now_provider: Callable[[], datetime]) -> tuple[dict[str, Any], bool]:
    """Publish a BODY/RULE/AI result and terminal state under one locked attempt.

    The caller owns the native transaction and performs wake-up after commit.
    Fetching, rule computation and paid inference finish outside this boundary.
    """
    marker = "%s" if postgres else "?"

    def execute(sql: str, parameters: tuple[object, ...] = ()):
        cursor = db if postgres else db.cursor()
        cursor.execute(sql.replace("?", marker), parameters)
        return cursor

    row = execute(
        "SELECT article_revision_id,target_id,stage,attempts,state,output_ref,payload_json,error "
        "FROM central_news_jobs WHERE job_key=?" + (" FOR UPDATE" if postgres else ""),
        (job_key,),
    ).fetchone()
    if row is None or int(row[3]) != attempts:
        return {"state": "stale", "output_ref": ""}, False
    article_id, target_id, stage, _, state, prior_ref, payload_raw, prior_error = row
    if stage not in {"BODY", "RULE", "AI"} or result.get("stage") != stage:
        raise ValueError("뉴스 결과와 작업 단계가 일치하지 않습니다.")
    if state in {"COMPLETED", "FAILED"}:
        receipt = {"state": f"already_{str(state).lower()}", "output_ref": str(prior_ref)}
        if prior_error == "manual_reanalysis_required":
            receipt["reason"] = prior_error
        return receipt, False
    if state != "RUNNING":
        return {"state": "stale", "output_ref": ""}, False
    now = now_provider().timestamp()

    if result.get("check_unexecuted_reanalysis") is True:
        if stage != "AI":
            raise ValueError("자동 재분석 확인은 AI 작업에만 적용합니다.")
        payload = json_mapping(payload_raw)
        previous = execute(
            "SELECT a.identity,b.body_revision_id,b.content_hash,d.document_json "
            "FROM central_news_article_revisions a "
            "JOIN central_news_body_revisions b ON b.article_revision_id=a.article_revision_id AND b.body_revision_id=? "
            "JOIN central_documents d ON d.collection='news_ai' AND d.owner=? AND d.document_key=a.identity "
            "WHERE a.article_revision_id=?",
            (str(payload.get("body_revision_id") or ""), str(target_id), str(article_id)),
        ).fetchone()
        if previous is None:
            return {"state": "continue", "output_ref": ""}, False
        identity, body_id, body_hash, document = previous
        observed = {
                "version": 1, "article_revision_id": str(article_id),
                "body_revision_id": str(body_id), "body_content_hash": str(body_hash),
            }
        if news_ai_source_version_status({str(identity): observed}, json_mapping(document).get("provenance_json")) == "CURRENT":
            return {"state": "continue", "output_ref": ""}, False
        # Work completion is distinct from an analysis result. No new-BODY
        # analysis ref, inference, usage or projection is fabricated here.
        changed = execute(
            "UPDATE central_news_jobs SET state='COMPLETED',output_ref='',error=?,updated_at=? "
            "WHERE job_key=? AND state='RUNNING' AND attempts=?",
            ("manual_reanalysis_required", now, job_key, attempts),
        ).rowcount
        if changed != 1:
            raise RuntimeError("뉴스 작업 종결 중 작업 소유권이 변경되었습니다.")
        return {"state": "completed", "output_ref": "", "reason": "manual_reanalysis_required"}, False

    def save_document(collection: str, owner: str, key: str, document: dict[str, Any]) -> None:
        inequality = ("central_documents.document_json IS DISTINCT FROM excluded.document_json"
                      if postgres else "central_documents.document_json<>excluded.document_json")
        execute(
            "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
            "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
            "updated_at=excluded.updated_at,document_json=excluded.document_json WHERE " + inequality,
            document_value_rows(collection, [{"owner": owner, "key": key, "document": document}], now)[0],
        )

    save_body = _save_postgres_news_body if postgres else _save_sqlite_news_body
    failed = "error" in result
    output_ref, wake = "", False
    accepted_provenance = None
    if failed:
        terminal = "FAILED" if attempts >= 3 else "PENDING"
        error = str(result["error"])[:1000]
        # AI settlement can happen after its Runner waiter has returned. Keep
        # the existing retry policy here with the actual native acceptance clock.
        next_retry_at = (now + min(60.0, float(2 ** min(attempts, 5)))
                         if stage == "AI" else float(result["next_retry_at"]))
        if stage == "BODY" and terminal == "FAILED":
            output_ref = save_body(db, {
                "article_revision_id": article_id, "status": "failed", "body_text": "",
                "fetched_at": now, "error": error,
            }, now=now)["body_revision_id"]
            wake = True
        changed = execute(
            "UPDATE central_news_jobs SET state=?,error=?,next_retry_at=?,output_ref=?,updated_at=? "
            "WHERE job_key=? AND state='RUNNING' AND attempts=?",
            (terminal, error, next_retry_at, output_ref, now, job_key, attempts),
        ).rowcount
        wake = wake or terminal == "PENDING"
    else:
        if stage == "BODY":
            output_ref = str(result.get("output_ref") or "")
            if output_ref:
                body = execute(
                    "SELECT article_revision_id FROM central_news_body_revisions WHERE body_revision_id=?",
                    (output_ref,),
                ).fetchone()
                if body is None or str(body[0]) != str(article_id):
                    raise ValueError("본문 결과가 작업의 기사 판본과 다릅니다.")
            else:
                body_value = dict(result["body"])
                body_value["article_revision_id"] = article_id
                output_ref = save_body(db, body_value, now=now)["body_revision_id"]
                wake = True
                if publication := result.get("original_publication"):
                    save_document("news_original_publication", str(article_id), "published_at", publication)
        elif stage == "RULE":
            payload = json_mapping(payload_raw)
            body_id = str(payload.get("body_revision_id") or "")
            assessment = dict(result["assessment"])
            if (str(assessment.get("article_revision_id")) != str(article_id)
                    or str(assessment.get("body_revision_id")) != body_id):
                raise ValueError("규칙 결과가 작업의 기사 또는 본문 판본과 다릅니다.")
            save_document("news_assessment", str(target_id), str(article_id), assessment)
            event_value = result.get("event")
            if event_value is None:
                output_ref = f"ignored:{body_id}"
            else:
                event_value = {**event_value, "article_revision_id": article_id,
                               "body_revision_id": body_id, "stock_code": target_id}
                save_event = _save_postgres_news_event if postgres else _save_sqlite_news_event
                output_ref = save_event(db, event_value, now=now)
        else:
            output_ref, accepted_provenance = _accept_news_ai_output(
                db, result, str(article_id), str(target_id), json_mapping(payload_raw),
                postgres=postgres, now=now, job_key=job_key, attempts=attempts,
            )
        changed = execute(
            "UPDATE central_news_jobs SET state='COMPLETED',output_ref=?,error='',updated_at=? "
            "WHERE job_key=? AND state='RUNNING' AND attempts=?",
            (output_ref, now, job_key, attempts),
        ).rowcount
        terminal = "COMPLETED"
    if changed != 1:
        raise RuntimeError("뉴스 결과 수락 중 작업 소유권이 변경되었습니다.")
    receipt = {"state": terminal.lower(), "output_ref": output_ref}
    if accepted_provenance is not None:
        receipt["analysis_provenance"] = accepted_provenance
    return receipt, wake


def _save_news_ai_results(db: Any, documents: list[dict[str, Any]], revisions: list[dict[str, Any]],
                          usage_documents: list[dict[str, Any]] | None, *, postgres: bool, now: float) -> list[dict[str, object]]:
    """Borrow one native cursor/connection; the public command owns commit."""
    if not postgres and not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")  # Current-source read and projection remain on one native writer cut.
    marker = "%s" if postgres else "?"
    inequality = ("central_documents.document_json IS DISTINCT FROM excluded.document_json"
                  if postgres else "central_documents.document_json<>excluded.document_json")
    sql = ("INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
           "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
           "updated_at=excluded.updated_at,document_json=excluded.document_json WHERE " + inequality).replace("?", marker)
    references = _save_news_ai_revisions(db, revisions, postgres=postgres, now=now)
    by_key = {
        (str(value["target_id"]), str(value["output"]["identity"])): reference
        for value, reference in zip(revisions, references, strict=True)
        if value["output"].get("identity")
    }
    published = []
    for value in documents:
        document = dict(value["document"])
        document.pop("provenance_json", None)
        reference = by_key.get((str(value["owner"]), str(value["key"])))
        if reference is None or reference["output_hash"] != stable_document_hash(document):
            claimed = value.get("input_provenance")
            reference = decode_news_ai_provenance(claimed)
            if reference.get("output_hash") != stable_document_hash(document):
                reference = {"status": "UNKNOWN"}
            reference["status"] = "UNKNOWN"
        published.append({**value, "document": {
            **document, "provenance_json": json.dumps(reference, ensure_ascii=False, separators=(",", ":")),
        }})
    if published:
        published = _select_current_news_ai_results(db, published, postgres=postgres)
        db.executemany(sql, document_value_rows("news_ai", published, now))
    if usage_documents:
        for usage in usage_documents:
            if "inference" in usage:
                _save_news_ai_inference(db, usage, postgres=postgres, now=now)
        db.executemany(sql, document_value_rows("news_request_usage", usage_documents, now))
    return references


def _select_current_news_ai_results(db: Any, submitted: list[dict[str, Any]], *, postgres: bool) -> list[dict[str, Any]]:
    """Retain native actual-input lineage matching the reader's current BODY.

    Immutable results have already been accepted in this transaction. Selecting
    their current projection never changes the submitted requester's receipt.
    """
    source_bound = {index for index, value in enumerate(submitted)
                    if _news_ai_has_source_binding(value["document"].get("provenance_json"))}
    if not source_bound:
        return submitted  # Legacy/generic values have no BODY lineage to compare.
    marker = "%s" if postgres else "?"
    def execute(sql, parameters):
        if postgres:
            db.execute(sql.replace("?", marker), parameters)
            return db
        return db.execute(sql, parameters)
    keys = {(str(submitted[index]["owner"]), str(submitted[index]["key"])) for index in source_bound}
    if postgres:
        # Ordered, transaction-local native locks cover absent as well as
        # existing projection rows. The next READ COMMITTED read is fresh.
        locks = sorted(f"news_ai_current:{len(owner)}:{owner}{identity}" for owner, identity in keys)
        execute("SELECT pg_advisory_xact_lock(hashtext(lock_key)) FROM "
                "(SELECT lock_key FROM (VALUES " + ",".join("(?)" for _ in locks) +
                ") AS requested(lock_key) ORDER BY lock_key) AS ordered", tuple(locks)).fetchall()

    sources, existing, histories = {}, {}, {}
    def member_keys(owner, reference):
        binding = decode_news_ai_provenance(reference).get("input_binding", {})
        return {(owner, str(member["identity"])) for member in binding.get("members", ())
                if isinstance(member, dict) and member.get("identity")}
    for index in source_bound:
        value = submitted[index]
        keys.update(member_keys(str(value["owner"]), value["document"].get("provenance_json")))

    def read_batch(requested):
        ordered = sorted(requested)
        columns = ",".join("r." + name for name in _NEWS_AI_REVISION_COLUMNS.split(","))
        sql = (
            "WITH wanted(owner,identity) AS (VALUES " + ",".join("(?,?)" for _ in ordered) + ") "
            "SELECT w.owner,w.identity,a.article_revision_id,b.body_revision_id,b.content_hash,d.document_json," + columns + " "
            "FROM wanted w LEFT JOIN central_news_article_revisions a ON a.accepted_sequence=COALESCE(("
            "SELECT latest.accepted_sequence FROM central_news_article_revisions latest "
            "WHERE latest.identity=w.identity AND latest.stock_code=w.owner AND latest.collection_scope='watchlist' "
            "ORDER BY latest.accepted_sequence DESC LIMIT 1),("
            "SELECT latest.accepted_sequence FROM central_news_article_revisions latest "
            "WHERE latest.identity=w.identity AND latest.stock_code='GLOBAL' "
            "AND EXISTS(SELECT 1 FROM central_news_article_target_revisions t "
            "WHERE t.article_revision_id=latest.article_revision_id AND t.stock_code=w.owner AND t.relation_status='confirmed') "
            "ORDER BY latest.accepted_sequence DESC LIMIT 1)) "
            "LEFT JOIN central_news_body_revisions b ON b.accepted_sequence=("
            "SELECT latest.accepted_sequence FROM central_news_body_revisions latest "
            "WHERE latest.article_revision_id=a.article_revision_id ORDER BY latest.accepted_sequence DESC LIMIT 1) "
            "LEFT JOIN central_documents d ON d.collection='news_ai' AND d.owner=w.owner AND d.document_key=w.identity "
            "LEFT JOIN central_news_ai_revisions r ON r.target_id=w.owner AND r.article_revision_id=a.article_revision_id "
            "AND r.body_revision_id=b.body_revision_id "
            "ORDER BY w.owner,w.identity,r.accepted_sequence DESC"
        )
        for row in execute(sql, tuple(part for key in ordered for part in key)).fetchall():
            key = (str(row[0]), str(row[1]))
            sources[key] = {"version": 1, "article_revision_id": row[2], "body_revision_id": row[3], "body_content_hash": row[4]}
            existing[key] = json_mapping(row[5]) if row[5] is not None else {}
            if row[6] is not None:
                revision = _decode_news_history("ai", [row[6:]])[0]
                histories.setdefault(key, []).append((revision["output"], _news_ai_provenance(revision)))
    read_batch(keys)
    additional = set()
    for key, value in existing.items():
        additional.update(member_keys(key[0], value.get("provenance_json")))
    for key, values in histories.items():
        for _document, reference in values:
            additional.update(member_keys(key[0], reference))
    if additional - keys:
        read_batch(additional - keys)  # One batch for older bundle memberships, never per displayed article.
    selected = []
    for index, value in enumerate(submitted):
        if index not in source_bound:
            selected.append(value)
            continue
        owner, identity = str(value["owner"]), str(value["key"])
        current_sources = {key[1]: source for key, source in sources.items() if key[0] == owner}
        prior = existing.get((owner, identity), {})
        if news_ai_source_version_status(current_sources, value["document"].get("provenance_json")) != "PREVIOUS":
            # Only a proven BODY mismatch permits no-rewind substitution.
            # Unverified generic edits keep their value and UNKNOWN provenance.
            selected.append(value)
            continue
        candidates = [(value["document"], value["document"].get("provenance_json")),
                      (prior, prior.get("provenance_json")), *histories.get((owner, identity), ())]
        chosen = next(((document, reference) for document, reference in candidates if document
                       and news_ai_source_version_status(current_sources, reference) == "CURRENT"), None)
        if chosen is None:
            selected.append(value)  # No matching result: retain the existing previous-judgment behavior.
        else:
            document, reference = chosen
            selected.append({**value, "document": {**document, "provenance_json": json.dumps(
                decode_news_ai_provenance(reference), ensure_ascii=False, separators=(",", ":"))}})
    return selected


def _native_news_catalog_values(db, values, *, postgres, authority, article_copy=True):
    """One bulk read proves source cuts from this native ledger, not copy claims.

    This decorates mutable read/copy projections. Original article/BODY rows and
    their canonical hashes are never amended by a source carrier.
    """
    if not values:
        return []
    keys = sorted({(str(value["owner"]), str(value["key"])) for value in values})
    marker = "%s" if postgres else "?"
    namespace = ("(SELECT nspname FROM pg_namespace WHERE oid=(SELECT relnamespace FROM pg_class "
                 "WHERE oid='central_news_article_revisions'::regclass))" if postgres else "'main'")
    sql = (
        "WITH wanted(owner,identity) AS (VALUES " + ",".join("(?,?)" for _ in keys) + ") "
        "SELECT w.owner,w.identity,a.article_revision_id,a.content_hash,a.document_json,"
        "b.body_revision_id,b.content_hash,b.accepted_sequence,b.status,b.extractor_version,b.fetched_at,b.available_at,"
        "a.available_at,a.published_at,t.stock_name," + namespace + " "
        "FROM wanted w LEFT JOIN central_news_article_revisions a ON a.accepted_sequence=COALESCE(("
        "SELECT latest.accepted_sequence FROM central_news_article_revisions latest "
        "WHERE latest.identity=w.identity AND latest.stock_code=w.owner AND latest.collection_scope='watchlist' "
        "ORDER BY latest.accepted_sequence DESC LIMIT 1),("
        "SELECT latest.accepted_sequence FROM central_news_article_revisions latest WHERE latest.identity=w.identity "
        "AND latest.stock_code='GLOBAL' AND EXISTS(SELECT 1 FROM central_news_article_target_revisions target "
        "WHERE target.article_revision_id=latest.article_revision_id AND target.stock_code=w.owner AND target.relation_status='confirmed') "
        "ORDER BY latest.accepted_sequence DESC LIMIT 1)) "
        "LEFT JOIN central_news_body_revisions b ON b.accepted_sequence=("
        "SELECT latest.accepted_sequence FROM central_news_body_revisions latest WHERE latest.article_revision_id=a.article_revision_id "
        "ORDER BY latest.accepted_sequence DESC LIMIT 1) "
        "LEFT JOIN central_news_article_target_revisions t ON t.accepted_sequence=("
        "SELECT latest.accepted_sequence FROM central_news_article_target_revisions latest "
        "WHERE latest.article_revision_id=a.article_revision_id AND latest.stock_code=w.owner AND latest.relation_status='confirmed' "
        "ORDER BY latest.accepted_sequence DESC LIMIT 1)"
    )
    if postgres:
        db.execute(sql.replace("?", marker), tuple(part for key in keys for part in key))
        rows = db.fetchall()
    else:
        rows = db.execute(sql, tuple(part for key in keys for part in key)).fetchall()
    contexts = {}
    for row in rows:
        source = None
        if row[2] and row[5]:
            source = {"version": 1, "article_revision_id": row[2], "article_content_hash": row[3],
                      "article_available_at": row[12], "article_published_at": row[13],
                      "body_revision_id": row[5], "body_content_hash": row[6], "body_status": row[8],
                      "body_extractor_version": row[9], "body_fetched_at": row[10], "body_available_at": row[11],
                      "native_sequence": {"authority": stable_document_hash({"database": authority, "namespace": row[15]}), "anchor": row[2], "body_ref": row[5],
                                          "body_hash": row[6], "sequence": int(row[7])}}
        article = json_mapping(row[4]) if row[4] is not None else {}
        contexts[(str(row[0]), str(row[1]))] = (source, str(row[14] or article.get("stock_name") or ""), article)
    projected = []
    for value in values:
        source, name, original = contexts[(str(value["owner"]), str(value["key"]))]
        document = dict(value.get("document") or {})
        if article_copy and source is not None:
            reported = decode_news_input_source(document.get("input_source_json"))
            matches = news_projection_article_hash(document, "local_projection_sync") == source["article_content_hash"] or (
                reported is not None and reported.get("article_revision_id") == source["article_revision_id"]
                and reported.get("article_content_hash") == source["article_content_hash"]
                and all(str(document.get(name) or "") == str(original.get(name) or "") for name in ("title", "description", "link", "original_link")))
            source = decode_news_input_source({**source, **({} if matches else {"status": "UNKNOWN"})})
            claimed = reported.get("reported_source", reported) if reported is not None else None
            selected = {**source, **({"reported_source": claimed} if claimed is not None and claimed != source else {})}
            document["input_source_json"] = json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
            document["input_source_contract"] = "v1"
        elif article_copy:
            reported = decode_news_input_source(document.get("input_source_json"))
            if reported is not None and "native_sequence" in reported:
                unverified = {name: value for name, value in reported.items() if name != "native_sequence"}
                unverified.update(status="UNKNOWN", reported_source=reported)
                document["input_source_json"] = json.dumps(unverified, ensure_ascii=False, separators=(",", ":"))
        projected.append({**value, "document": document, "native_source_cut": source, "target_name": name})
    return projected


def _protect_reported_news_ai(db, collection, values, *, postgres, authority):
    """Apply the same source/current boundary to actual automatic catalog copies."""
    if not values:
        return []
    marker = "%s" if postgres else "?"
    def execute(sql, parameters):
        if postgres:
            db.execute(sql.replace("?", marker), parameters)
            return db
        return db.execute(sql, parameters)
    identities = sorted({str(value["key"]) for value in values})
    if postgres:
        locks = sorted("news_ai_shared_current:" + identity for identity in identities)
        execute("SELECT pg_advisory_xact_lock(hashtext(lock_key)) FROM (SELECT lock_key FROM (VALUES " +
                ",".join("(?)" for _ in locks) + ") AS requested(lock_key) ORDER BY lock_key) AS ordered", tuple(locks)).fetchall()
    shared = {str(row[0]): json_mapping(row[1]) for row in execute(
        "SELECT document_key,document_json FROM central_documents WHERE collection='news_ai_shared' AND owner='shared' "
        "AND document_key IN (" + ",".join("?" for _ in identities) + ")", tuple(identities)).fetchall()}
    keys = set()
    for value in values:
        reference = decode_news_ai_provenance(value["document"].get("provenance_json"))
        owner = str(value["owner"]) if collection == "news_ai" else str(reference.get("target_id") or "")
        keys.add((owner, str(value["key"])))
        for ref in (reference, decode_news_ai_provenance(shared.get(str(value["key"]), {}).get("provenance_json"))):
            origin = str(ref.get("target_id") or owner)
            for member in ref.get("input_binding", {}).get("members", ()):
                if isinstance(member, dict) and member.get("identity"):
                    keys.add((origin, str(member["identity"])))
                    if collection == "news_ai":
                        keys.add((owner, str(member["identity"])))
    contexts = _native_news_catalog_values(db, [{"owner": owner, "key": identity, "document": {}}
        for owner, identity in sorted(keys)], postgres=postgres, authority=authority, article_copy=False)
    frames, names = {}, {}
    for value in contexts:
        frames.setdefault(str(value["owner"]), {})[str(value["key"])] = value["native_source_cut"]
        names[str(value["owner"])] = value["target_name"] or names.get(str(value["owner"]), "")
    own = {}
    if collection == "news_ai":
        wanted = sorted({(str(value["owner"]), str(value["key"])) for value in values})
        rows = execute("WITH wanted(owner,identity) AS (VALUES " + ",".join("(?,?)" for _ in wanted) + ") "
            "SELECT d.owner,d.document_key FROM central_documents d JOIN wanted w ON w.owner=d.owner AND w.identity=d.document_key "
            "WHERE d.collection='news_ai'", tuple(part for key in wanted for part in key)).fetchall()
        own = {(str(row[0]), str(row[1])): True for row in rows}
    selected = []
    for value in values:
        identity, owner = str(value["key"]), str(value["owner"])
        prior = shared.get(identity, {})
        reference = decode_news_ai_provenance(prior.get("provenance_json"))
        incoming = value["document"].get("provenance_json")
        if collection == "news_ai_shared":
            if any(news_ai_source_version_status(frame, reference) == "CURRENT" and news_ai_source_version_status(frame, incoming) != "CURRENT"
                   for frame in frames.values()):
                value = {**value, "document": prior}
        elif (owner, identity) not in own:
            # Exactly the existing load_many company-impact fallback, without
            # promoting an A result into a fabricated B own binding.
            applies = news_ai_shared_applies(prior.get("company_impacts"), names.get(owner, ""))
            frame = frames.get(owner, {})
            if applies and news_ai_source_version_status(frame, reference) == "CURRENT" and news_ai_source_version_status(frame, incoming) != "CURRENT":
                continue
        selected.append(value)
    return selected


def _save_news_ai_inference(db: Any, usage: dict[str, Any], *, postgres: bool, now: float) -> None:
    """One immutable produced input/output; generic sync cannot overwrite it."""
    value = usage["inference"]
    inference_id = str(value["inference_id"])
    content_hash = stable_document_hash(value)
    if (str(value["usage_owner"]) != str(usage["owner"]) or str(value["usage_key"]) != str(usage["key"])
            or usage["document"].get("inference_ref") != {"inference_id": inference_id, "content_hash": content_hash}):
        raise ValueError("AI inference와 usage receipt의 관계가 다릅니다.")
    marker = "%s" if postgres else "?"
    def execute(sql, parameters):
        if postgres:
            db.execute(sql.replace("?", marker), parameters)
            return db
        return db.execute(sql, parameters)
    inserted = execute("INSERT INTO central_news_ai_inferences(inference_id,target_id,content_hash,computed_at,available_at,document_json) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(inference_id) DO NOTHING",
            (inference_id, str(value["target_id"]), content_hash, float(value["computed_at"]), now,
             json.dumps(value, ensure_ascii=False, separators=(",", ":")))).rowcount
    if inserted == 0:
        row = execute("SELECT content_hash,document_json FROM central_news_ai_inferences WHERE inference_id=?",
                      (inference_id,)).fetchone()
        if row is None or str(row[0]) != content_hash or stable_document_hash(json_mapping(row[1])) != content_hash:
            raise ValueError("AI inference ID의 기존 입력·출력 원본과 다릅니다.")
    # Resolve immutable-ID conflicts before validating a newly submitted envelope.
    # Fresh invalid inserts still roll back before the command owner's COMMIT.
    if "input_envelope_version" in value:
        if type(value["input_envelope_version"]) is not int or value["input_envelope_version"] != 1:
            raise ValueError("AI inference input envelope version differs")
        for produced_input in value["inputs"]:
            validate_news_ai_input(produced_input)


def _save_news_ai_revisions(db: Any, values: list[dict[str, Any]], *, postgres: bool, now: float) -> list[dict[str, object]]:
    marker = "%s" if postgres else "?"
    columns = _NEWS_AI_REVISION_COLUMNS
    references: list[dict[str, object]] = []
    def execute(sql, parameters):
        if postgres:
            db.execute(sql.replace("?", marker), parameters)
            return db
        return db.execute(sql, parameters)
    for value in values:
        parameters = (
            str(value.get("analysis_revision_id") or uuid.uuid4().hex), str(value["target_id"]),
            str(value["article_revision_id"]), value.get("body_revision_id"), str(value["provider"]),
            str(value["model"]), str(value["prompt_version"]),
            str(value.get("schema_version") or NEWS_ANALYSIS_SCHEMA_VERSION), str(value["input_hash"]),
            float(value.get("computed_at") or now), now,
            json.dumps(value["output"], ensure_ascii=False, separators=(",", ":")),
            json.dumps(value.get("usage", {}), ensure_ascii=False, separators=(",", ":")),
        )
        inserted = execute(
            "INSERT INTO central_news_ai_revisions(" + columns + ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(analysis_revision_id) DO NOTHING", parameters,
        ).rowcount
        row = parameters
        if inserted == 0:
            row = execute("SELECT " + columns + " FROM central_news_ai_revisions WHERE analysis_revision_id=?", (parameters[0],)).fetchone()
            if (row is None or tuple(row[:10]) != parameters[:10]
                    or stable_document_hash(json_mapping(row[11])) != stable_document_hash(value["output"])
                    or stable_document_hash(json_mapping(row[12])) != stable_document_hash(value.get("usage", {}))):
                raise ValueError("AI 분석 판본 ID의 기존 원본과 재수락 내용이 다릅니다.")
        references.append(_news_ai_provenance(_decode_news_history("ai", [row])[0]))
    return references


def _accept_news_ai_output(db: Any, result: dict[str, Any], article_id: str, target_id: str,
                           payload: dict[str, Any], *, postgres: bool, now: float,
                           job_key: str, attempts: int) -> tuple[str, dict[str, object] | None]:
    marker = "%s" if postgres else "?"
    def query(sql, parameters):
        if postgres:
            db.execute(sql.replace("?", marker), parameters)
            return db.fetchone()
        return db.execute(sql, parameters).fetchone()
    body_id = str(payload.get("body_revision_id") or "")
    binding = result["binding"]
    if (str(binding["article_revision_id"]) != article_id
            or str(binding["body_revision_id"]) != body_id
            or str(binding["target_id"]) != target_id):
        raise ValueError("AI 결과가 작업의 대상 또는 기사·본문 판본과 다릅니다.")
    if result.get("cached"):
        row = query(
            "SELECT " + _NEWS_AI_REVISION_COLUMNS + " FROM central_news_ai_revisions WHERE target_id=? "
            "AND article_revision_id=? AND body_revision_id=? AND provider=? AND model=? "
            "AND prompt_version=? AND schema_version=? AND input_hash=? ORDER BY accepted_sequence DESC LIMIT 1",
            (target_id, article_id, body_id, binding["provider"], binding["model"],
             binding["prompt_version"], binding["schema_version"], binding["input_hash"]),
        )
        return ((str(row[0]), _news_ai_provenance(_decode_news_history("ai", [row])[0]))
                if row else (str(result["fallback_ref"]), None))
    usage = result["usage_ref"]
    stored = query(
        "SELECT document_json FROM central_documents WHERE collection='news_request_usage' AND owner=? AND document_key=?",
        (usage["owner"], usage["key"]),
    )
    if stored is None or stable_document_hash(json_mapping(stored[0])) != stable_document_hash(usage["document"]):
        raise RuntimeError("AI 실제 요청 usage receipt가 아직 확인되지 않았습니다.")
    revisions, documents = result["revisions"], result["documents"]
    if len(revisions) != 1 or len(documents) != 1:
        raise ValueError("AI 작업 결과는 해당 pinned 기사 한 건이어야 합니다.")
    revision = revisions[0]
    if any(str(revision[name]) != str(binding[name]) for name in (
        "target_id", "article_revision_id", "body_revision_id", "provider", "model", "prompt_version", "schema_version", "input_hash",
    )):
        raise ValueError("AI 불변 결과와 작업 입력 판본이 다릅니다.")
    authority = revision["usage"].get("requester")
    if authority != {"kind": "job", "job_key": job_key, "attempts": attempts, "binding": binding}:
        raise ValueError("AI requester의 native 작업·attempt 권한이 다릅니다.")
    ref = revision["usage"].get("inference_ref", {})
    inference_row = query("SELECT content_hash,document_json FROM central_news_ai_inferences WHERE inference_id=?",
                          (str(ref.get("inference_id") or ""),))
    if inference_row is None or str(inference_row[0]) != str(ref.get("content_hash")):
        raise ValueError("AI 실제 inference 원본을 확인하지 못했습니다.")
    inference = json_mapping(inference_row[1])
    if (stable_document_hash(inference) != str(ref["content_hash"])
            or str(inference["usage_owner"]) != str(usage["owner"])
            or str(inference["usage_key"]) != str(usage["key"])
            or any(str(inference[name]) != str(binding[name]) for name in
                   ("target_id", "provider", "model", "prompt_version", "schema_version"))):
        raise ValueError("AI inference와 결과의 출처·판본·usage가 다릅니다.")
    index = int(ref["input_index"])
    if index < 0 or index >= len(inference["inputs"]) or index >= len(inference["outputs"]):
        raise ValueError("AI inference 입력 위치가 다릅니다.")
    if (str(inference["inputs"][index]["input_hash"]) != str(binding["input_hash"])
            or stable_document_hash(revision["output"]) != stable_document_hash(inference["outputs"][index])):
        raise ValueError("AI 결과가 실제 inference 입력·출력과 다릅니다.")
    if (inference.get("input_envelope_version") == 1
            and stable_document_hash(revision["usage"].get("input_binding", {}))
            != stable_document_hash(inference["inputs"][index]["input_binding"])):
        raise ValueError("AI accepted source binding differs from the actual produced input")
    document = documents[0]
    if (str(document["owner"]) != target_id
            or str(document["key"]) != str(payload["event"]["identity"])
            or stable_document_hash(document["document"]) != stable_document_hash(revision["output"])):
        raise ValueError("AI 현재 결과가 작업의 대상·기사 또는 불변 결과와 다릅니다.")
    references = _save_news_ai_results(db, documents, revisions, None, postgres=postgres, now=now)
    return str(revision["analysis_revision_id"]), references[0]


def _save_sqlite_news_body(connection: sqlite3.Connection, value: dict[str, Any], *,
                           now: float | None = None) -> dict[str, Any]:
    # Existing standalone/external writers retain their compatibility clock.
    # Attempt-owned commands supply the store's corrected clock explicitly.
    accepted_at = time() if now is None else now
    revision_id = uuid.uuid4().hex
    body = str(value.get("body_text") or "")
    content_hash = stable_document_hash({"body": body})
    article_revision_id = str(value["article_revision_id"])
    connection.execute(
        "INSERT INTO central_news_body_revisions(body_revision_id,article_revision_id,content_hash,"
        "extractor_version,fetched_at,available_at,status,body_text,error) VALUES(?,?,?,?,?,?,?,?,?)",
        (revision_id, article_revision_id, content_hash,
         str(value.get("extractor_version") or ARTICLE_BODY_EXTRACTOR_VERSION),
         float(value.get("fetched_at") or accepted_at), accepted_at, str(value["status"]), body,
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
            accepted_at, revision_id,
        )
    return {
        "body_revision_id": revision_id, "article_revision_id": article_revision_id,
        "content_hash": content_hash, "extractor_version": str(value.get("extractor_version") or ARTICLE_BODY_EXTRACTOR_VERSION),
        "fetched_at": float(value.get("fetched_at") or accepted_at), "available_at": accepted_at,
        "status": str(value["status"]),
    }


def _save_postgres_news_body(cursor: Any, value: dict[str, Any], *,
                             now: float | None = None) -> dict[str, Any]:
    accepted_at = time() if now is None else now
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
         float(value.get("fetched_at") or accepted_at), accepted_at, str(value["status"]), body,
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
            accepted_at, revision_id,
        )
    return {
        "body_revision_id": revision_id, "article_revision_id": article_revision_id,
        "content_hash": content_hash, "extractor_version": str(value.get("extractor_version") or ARTICLE_BODY_EXTRACTOR_VERSION),
        "fetched_at": float(value.get("fetched_at") or accepted_at), "available_at": accepted_at,
        "status": str(value["status"]),
    }


def _save_sqlite_news_ai(connection: sqlite3.Connection, values: list[dict[str, Any]], *, now: float | None = None) -> None:
    _save_news_ai_revisions(connection, values, postgres=False, now=time() if now is None else now)


def _save_postgres_news_ai(cursor: Any, values: list[dict[str, Any]], *, now: float | None = None) -> None:
    _save_news_ai_revisions(cursor, values, postgres=True, now=time() if now is None else now)


def _save_sqlite_news_event(connection: sqlite3.Connection, value: dict[str, Any], *,
                            now: float | None = None) -> str:
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
    event_revision_id, available_at = uuid.uuid4().hex, time() if now is None else now
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


def _save_postgres_news_event(cursor: Any, value: dict[str, Any], *,
                              now: float | None = None) -> str:
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
    event_revision_id, available_at = uuid.uuid4().hex, time() if now is None else now
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
                        available_at: float | None, limit: int,
                        before_revision_id: str = "") -> tuple[str, list[object]]:
    if kind == "article":
        sql = ("SELECT article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
               "received_at,available_at,collection_scope,revision_of,document_json "
               "FROM central_news_article_revisions WHERE 1=1")
        target_column, identity_column = "stock_code", "identity"
    elif kind == "inference":
        sql = ("SELECT inference_id,target_id,content_hash,computed_at,available_at,document_json "
               "FROM central_news_ai_inferences WHERE 1=1")
        target_column, identity_column = "target_id", "inference_id"
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
    if before_revision_id:
        table, revision_column = {
            "article": ("central_news_article_revisions", "article_revision_id"),
            "body": ("central_news_body_revisions", "body_revision_id"),
            "inference": ("central_news_ai_inferences", "inference_id"),
            "ai": ("central_news_ai_revisions", "analysis_revision_id"),
            "event": ("central_news_event_revisions", "event_revision_id"),
            "membership": ("central_news_event_membership_revisions", "membership_revision_id"),
        }[kind]
        sql += (f" AND accepted_sequence<(SELECT accepted_sequence FROM {table} "
                f"WHERE {revision_column}={placeholder})")
        parameters.append(before_revision_id)
    sql += f" ORDER BY accepted_sequence DESC LIMIT {placeholder}"
    parameters.append(bounded_limit(limit, 1000))
    return sql, parameters


def _decode_news_history(kind: str, rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    if kind == "article":
        keys = ("article_revision_id", "stock_code", "identity", "content_hash", "collector_id",
                "published_at", "received_at", "available_at", "collection_scope", "revision_of")
        return [{**dict(zip(keys, row[:10], strict=True)),
                 "document": row[10] if isinstance(row[10], dict) else json.loads(str(row[10]))} for row in rows]
    if kind == "inference":
        keys = ("inference_id", "target_id", "content_hash", "computed_at", "available_at")
        return [{**dict(zip(keys, row[:5], strict=True)), "document": json_mapping(row[5])} for row in rows]
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
                              identity: str, available_at: float | None, limit: int,
                              before_revision_id: str = "") -> list[dict[str, Any]]:
    sql, parameters = _news_history_query(kind, "?", target, identity, available_at, limit, before_revision_id)
    return _decode_news_history(kind, connection.execute(sql, parameters).fetchall())


def _load_postgres_news_history(cursor: Any, kind: str, target: str, identity: str,
                                available_at: float | None, limit: int,
                                before_revision_id: str = "") -> list[dict[str, Any]]:
    sql, parameters = _news_history_query(kind, "%s", target, identity, available_at, limit, before_revision_id)
    cursor.execute(sql, parameters)
    return _decode_news_history(kind, cursor.fetchall())


def _confirmed_news_articles_query(placeholder: str, *, postgres: bool = False) -> str:
    published_order = "ranked.published_at::timestamptz" if postgres else "julianday(ranked.published_at)"
    return (
        "WITH ranked AS ("
        "SELECT a.document_json,a.accepted_sequence,a.article_revision_id,a.published_at,a.identity,a.content_hash,a.available_at,a.stock_code,"
        "ROW_NUMBER() OVER(PARTITION BY a.identity ORDER BY a.accepted_sequence DESC) AS identity_rank "
        "FROM central_news_article_target_revisions t "
        "JOIN central_news_article_revisions a ON a.article_revision_id=t.article_revision_id "
        f"WHERE t.stock_code={placeholder} AND t.relation_status='confirmed' AND a.stock_code='GLOBAL') "
        "SELECT ranked.document_json,b.status,b.body_text,d.document_json,"
        "ranked.article_revision_id,ranked.content_hash,ranked.available_at,"
        "b.body_revision_id,b.content_hash,b.extractor_version,b.fetched_at,b.available_at,c.document_json "
        "FROM ranked LEFT JOIN central_news_body_revisions b ON b.accepted_sequence=("
        "SELECT latest.accepted_sequence FROM central_news_body_revisions latest "
        "WHERE latest.article_revision_id=ranked.article_revision_id "
        "ORDER BY latest.accepted_sequence DESC LIMIT 1) "
        "LEFT JOIN central_documents c ON c.collection='news_article' "
        "AND c.owner=ranked.stock_code AND c.document_key=ranked.identity "
        "LEFT JOIN central_documents d ON d.collection='news_assessment' "
        f"AND d.owner={placeholder} AND d.document_key=ranked.article_revision_id "
        "WHERE identity_rank=1 "
        f"ORDER BY {published_order} DESC NULLS LAST,ranked.identity ASC LIMIT {placeholder}"
    )


def _stock_news_articles_query(placeholder: str, *, postgres: bool = False) -> str:
    published_order = "ranked.published_at::timestamptz" if postgres else "julianday(ranked.published_at)"
    return (
        "WITH ranked AS ("
        "SELECT a.document_json,a.accepted_sequence,a.article_revision_id,a.published_at,a.identity,a.content_hash,a.available_at,a.stock_code,"
        "ROW_NUMBER() OVER(PARTITION BY a.identity ORDER BY a.accepted_sequence DESC) AS identity_rank "
        "FROM central_news_article_revisions a "
        f"WHERE a.stock_code={placeholder} AND a.collection_scope='watchlist') "
        "SELECT ranked.document_json,b.status,b.body_text,d.document_json,"
        "ranked.article_revision_id,ranked.content_hash,ranked.available_at,"
        "b.body_revision_id,b.content_hash,b.extractor_version,b.fetched_at,b.available_at,c.document_json "
        "FROM ranked LEFT JOIN central_news_body_revisions b ON b.accepted_sequence=("
        "SELECT latest.accepted_sequence FROM central_news_body_revisions latest "
        "WHERE latest.article_revision_id=ranked.article_revision_id "
        "ORDER BY latest.accepted_sequence DESC LIMIT 1) "
        "LEFT JOIN central_documents c ON c.collection='news_article' "
        "AND c.owner=ranked.stock_code AND c.document_key=ranked.identity "
        "LEFT JOIN central_documents d ON d.collection='news_assessment' "
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
            if len(row) >= 12:
                assessment = value.get("_assessment")
                value["_input_source"] = {
                    "version": 1, "article_revision_id": row[4], "article_content_hash": row[5],
                    "article_available_at": row[6], "article_published_at": value.get("published_at"),
                    "published_at_source": assessment.get("published_at_source") if isinstance(assessment, dict) else None,
                    "body_revision_id": row[7], "body_content_hash": row[8],
                    "body_status": row[1], "body_extractor_version": row[9],
                    "body_fetched_at": row[10], "body_available_at": row[11],
                }
                current = json_mapping(row[12]) if len(row) >= 13 and row[12] is not None else {}
                if (current.get("input_source_contract") == "v1"
                        and news_projection_article_hash(current, "local_projection_sync") == row[5]):
                    reported = decode_news_input_source(current.get("input_source_json"))
                    primary = value["_input_source"]
                    reported_core = ({name: item for name, item in reported.items()
                                      if name not in {"status", "reported_source"}}
                                     if reported is not None else None)
                    primary_core = primary
                    if (reported_core is not None and "reported_source" in reported
                            and native_news_source_sequence(reported) is not None):
                        # The native catalog adds a sequence to the envelope and
                        # leaves publisher provenance in the nested claim.
                        del reported_core["native_sequence"]
                        if "published_at_source" not in reported_core:
                            primary_core = {name: item for name, item in primary.items()
                                            if name != "published_at_source"}
                    if reported_core == primary_core:
                        # A native page echoed through the local sync is the
                        # same source evidence, not a new upstream observation.
                        reported = reported.get("reported_source")
                        if reported is not None and {name: item for name, item in reported.items()
                                                     if name != "status"} == primary:
                            reported = None
                    if reported is not None:
                        # Copied source evidence does not replace native refs or
                        # the selected revisions' first availability above.
                        value["_input_source"]["reported_source"] = reported
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


# BODY role commands share this existing revision/terminal transaction owner.
_NEWS_BODY_ELIGIBLE = ("stage='BODY' AND processing_version=? AND NOT EXISTS (SELECT 1 "
    "FROM central_news_article_revisions a WHERE a.article_revision_id=central_news_jobs.article_revision_id "
    f"AND a.collection_scope IN ({_NEWS_JOB_CLAIM_HISTORICAL_SCOPES_SQL}))")

_NEWS_BODY_COLUMNS = ("j.job_key,j.article_revision_id,j.stock_code,j.target_id,j.stage,j.input_hash,"
    "j.processing_version,j.attempts,j.payload_json,j.updated_at,j.body_processor_owner,j.state,"
    "j.output_ref,a.collection_scope,a.content_hash,j.body_input_ref,a.document_json,a.collector_id")

def _news_body_mapping(value):
    return value if isinstance(value,dict) else json.loads(value)

def _news_body_owner_binding(owner, attempts):
    # Local NAS claims stay byte-for-byte unchanged: their incremented attempt
    # cannot inherit an earlier remote principal's binding.
    return json.dumps([owner,attempts],ensure_ascii=False,separators=(',',':'))

def _news_body_source_hash(row):
    document=_news_body_mapping(row[16])
    payload=_news_body_mapping(row[8])
    # Current source-page writers own filtered hashes; generic article writers
    # own full-document hashes. Preserve their persisted provenance and bytes.
    source_page=(row[2]=='GLOBAL' and row[13] in {'query_set','naver_stock_market'} and row[17]=='naver')
    article_hash=news_article_content_hash(document) if source_page else news_projection_article_hash(document,row[13])
    if (article_hash!=row[14]
            or any(str(payload.get(key) or '')!=str(document.get(key) or '') for key in ('link','original_link','description'))):
        raise NewsBodyConflict('native BODY article or URL input differs from its revision')
    return stable_document_hash({'article_revision_id':row[1],'stock_code':row[2],'target_id':row[3],
        'input_hash':row[5],'processing_version':row[6],'payload':payload,'collection_scope':row[13],
        'article_hash':row[14],'collector_id':row[17]})

def _news_body_cached(db, ref, *, postgres):
    if not ref:
        return None
    row=_news_body_execute(db,"SELECT body_revision_id,article_revision_id,content_hash,extractor_version,fetched_at,available_at,status,body_text,error "
        "FROM central_news_body_revisions WHERE body_revision_id=?",(ref,),postgres=postgres).fetchone()
    if not row or stable_document_hash({'body':row[7]})!=row[2]:
        raise NewsBodyConflict('admitted BODY reference changed or disappeared')
    return row

def _news_body_execute(db, sql, values=(), *, postgres):
    if postgres:
        db.execute(sql.replace('?', '%s'), values)
        return db
    return db.execute(sql, values)

def _news_body_read(db, owner, key, attempts, *, postgres, lock=False):
    if not isinstance(owner, str) or not owner or type(attempts) is not int or attempts < 1:
        raise NewsBodyConflict("invalid BODY attempt authority")
    row = _news_body_execute(db, "SELECT " + _NEWS_BODY_COLUMNS + " FROM central_news_jobs j "
        "JOIN central_news_article_revisions a ON a.article_revision_id=j.article_revision_id "
        "WHERE j.job_key=?" + (" FOR UPDATE OF j" if postgres and lock else ""), (key,), postgres=postgres).fetchone()
    if (not row or row[4] != 'BODY' or row[6] != ARTICLE_BODY_EXTRACTOR_VERSION
            or row[7] != attempts or row[10] != _news_body_owner_binding(owner,attempts) or row[13] in {
                'historical_backfill','historical_market_backfill','historical_market_pc_backfill','historical_news_pc_backfill'}
            or row[5] != row[14]):
        raise NewsBodyConflict("BODY job owner, attempt or input differs")
    return row

def _news_body_claim(db, owner, now, *, postgres):
    if (not isinstance(owner,str) or not 1<=len(owner)<=96
            or any(not (character.isalnum() or character in '_-') for character in owner)
            or not math.isfinite(now)):
        raise NewsBodyConflict("invalid BODY claim authority")
    # Neither recovery nor attempt mutation can touch RULE/AI/historical rows.
    _news_body_execute(db, "UPDATE central_news_jobs SET state='PENDING',updated_at=? "
        "WHERE state='RUNNING' AND updated_at<? AND " + _NEWS_BODY_ELIGIBLE,
        (now, now-120.0, ARTICLE_BODY_EXTRACTOR_VERSION), postgres=postgres)
    row = _news_body_execute(db, "SELECT job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
        "processing_version,attempts,payload_json,updated_at FROM central_news_jobs "
        "WHERE state='PENDING' AND next_retry_at<=? AND " + _NEWS_BODY_ELIGIBLE +
        " ORDER BY CASE WHEN stock_code<>'GLOBAL' THEN 0 ELSE 1 END,updated_at DESC LIMIT 1"
        + (" FOR UPDATE SKIP LOCKED" if postgres else ""),
        (now, ARTICLE_BODY_EXTRACTOR_VERSION), postgres=postgres).fetchone()
    if not row:
        return None
    _news_body_execute(db, "UPDATE central_news_jobs SET state='RUNNING',attempts=attempts+1,updated_at=?,"
        "body_processor_owner=?,body_input_ref=NULL WHERE job_key=?", (now,_news_body_owner_binding(owner,int(row[7])+1),row[0]), postgres=postgres)
    return {**_news_job_rows([row])[0], 'owner': owner}

def _news_body_input(db, owner, key, attempts, *, postgres):
    row = _news_body_read(db,owner,key,attempts,postgres=postgres,lock=True)
    if row[11] != 'RUNNING':
        raise NewsBodyConflict("BODY input no longer belongs to a running attempt")
    source_hash=_news_body_source_hash(row)
    columns="SELECT body_revision_id,article_revision_id,content_hash,extractor_version,fetched_at,available_at,status,body_text,error FROM central_news_body_revisions "
    if row[15] is None:
        body=_news_body_execute(db,columns+"WHERE article_revision_id=? ORDER BY accepted_sequence DESC LIMIT 1",(row[1],),postgres=postgres).fetchone()
        if body and stable_document_hash({'body':body[7]})!=body[2]:
            raise NewsBodyConflict('native cached BODY bytes differ from their hash')
        pin={'source_hash':source_hash,'ref':body[0] if body else '',
             'body_hash':body[2] if body else '', 'body_status':body[6] if body else ''}
        _news_body_execute(db,"UPDATE central_news_jobs SET body_input_ref=? WHERE job_key=?",(json.dumps(pin,sort_keys=True,separators=(',',':')),key),postgres=postgres)
    else:
        pin=_news_body_mapping(row[15])
        if pin['source_hash']!=source_hash:
            raise NewsBodyConflict('admitted native BODY input changed during processing')
        body=_news_body_cached(db,pin['ref'],postgres=postgres)
        if body and (body[2]!=pin['body_hash'] or body[6]!=pin['body_status']):
            raise NewsBodyConflict('admitted BODY metadata changed')
    return {'job_key':key,'attempts':attempts,'owner':owner,'article_revision_id':row[1],
        'input_hash':row[5],'processing_version':row[6],'payload':_news_body_mapping(row[8]),'input_descriptor':source_hash,
        'cached_body':_decode_news_history('body',[body])[0] if body else None}

def _news_body_receipt(db, owner, key, attempts, *, postgres):
    row = _news_body_read(db,owner,key,attempts,postgres=postgres)
    receipt={'job_key':key,'attempts':attempts,'owner':owner,'state':row[11].lower(),'output_ref':row[12]}
    pin=_news_body_mapping(row[15]) if row[15] is not None else {}
    if 'accepted_envelope_hash' in pin and 'accepted_receipt' in pin:
        receipt.update(accepted_envelope_hash=pin['accepted_envelope_hash'],accepted_receipt=pin['accepted_receipt'])
    return receipt

def _news_body_accept(db, owner, key, envelope, *, postgres, now_provider):
    if not isinstance(envelope,dict) or set(envelope) != {'attempts','input_hash','processing_version','input_descriptor','payload','result'}:
        raise NewsBodyConflict("invalid BODY result envelope")
    attempts = envelope['attempts']
    row = _news_body_read(db,owner,key,attempts,postgres=postgres,lock=True)
    if row[15] is None:
        raise NewsBodyConflict("BODY input has not been admitted for this attempt")
    pin=_news_body_mapping(row[15])
    if (_news_body_source_hash(row)!=pin['source_hash'] or envelope['input_descriptor']!=pin['source_hash']
            or envelope['payload']!=_news_body_mapping(row[8])):
        raise NewsBodyConflict('BODY result native input, source or URL changed')
    cached=_news_body_cached(db,pin['ref'],postgres=postgres)
    if cached and (cached[1]!=row[1] or cached[2]!=pin['body_hash'] or cached[6]!=pin['body_status']):
        raise NewsBodyConflict('BODY cached input differs from the admitted reference')
    if envelope['input_hash'] != row[5] or envelope['processing_version'] != row[6]:
        raise NewsBodyConflict("BODY result input hash or processing version differs")
    accepted_hash=stable_document_hash({'owner':owner,'job_key':key,'envelope':envelope})
    if 'accepted_envelope_hash' in pin or 'accepted_receipt' in pin:
        first=pin.get('accepted_receipt')
        if (pin.get('accepted_envelope_hash')!=accepted_hash or not isinstance(first,dict)
                or type(first.get('attempts')) is not int
                or any(first.get(field)!=value for field,value in {'job_key':key,'attempts':attempts,'owner':owner}.items())
                or first.get('state') not in {'completed','failed','pending'} or not isinstance(first.get('output_ref'),str)
                or first['state']!=row[11].lower() or first['output_ref']!=row[12]):
            raise NewsBodyConflict('BODY result differs from the accepted envelope or first receipt')
        # Preserve the existing terminal duplicate response; pending errors need
        # their exact first receipt without another retry-clock read/settlement.
        return {**first,'state':('already_'+first['state'] if first['state'] in {'completed','failed'} else first['state'])},False
    result = envelope['result']
    if not isinstance(result,dict) or result.get('stage') != 'BODY':
        raise NewsBodyConflict("only BODY results are accepted")
    if 'error' in result:
        if set(result) - {'stage','error','next_retry_at'} or not isinstance(result['error'],str):
            raise NewsBodyConflict("invalid BODY failure")
        result = {'stage':'BODY','error':result['error'][:1000],
            'next_retry_at':now_provider().timestamp()+min(60.0,float(2**min(attempts,5)))}
    elif 'output_ref' in result:
        if set(result) != {'stage','output_ref'} or not isinstance(result['output_ref'],str):
            raise NewsBodyConflict("invalid cached BODY result")
        if not pin['ref'] or result['output_ref'] != pin['ref']:
            raise NewsBodyConflict("cached BODY ref differs from the admitted input")
        # Existing native acceptance verifies the cached ref belongs to this article.
    else:
        if cached and cached[6] in {'fulltext','summary_only'}:
            raise NewsBodyConflict('an admitted reusable BODY must retain its accepted reference')
        if set(result) - {'stage','body','original_publication'} or not isinstance(result.get('body'),dict):
            raise NewsBodyConflict("invalid BODY output")
        body=result['body']
        if (set(body) != {'extractor_version','fetched_at','status','body_text','error'}
                or body['extractor_version'] != row[6] or body['status'] not in {'fulltext','summary_only'}
                or not isinstance(body['body_text'],str) or not body['body_text'] or len(body['body_text'])>2_000_000
                or type(body['fetched_at']) not in (int,float) or not math.isfinite(body['fetched_at'])
                or body['error'] != ''):
            raise NewsBodyConflict("BODY output does not match the claimed processor contract")
        if 'original_publication' in result:
            publication=result['original_publication']
            if (not isinstance(publication,dict) or set(publication) != {'published_at','source_url','source','precision'}
                    or not all(isinstance(value,str) for value in publication.values())
                    or publication['source'] != 'article_html' or publication['precision'] != 'second'
                    or len(publication['source_url'])>2000 or len(publication['published_at'])>100):
                raise NewsBodyConflict("invalid original publication evidence")
            moment=datetime.fromisoformat(publication['published_at'])
            if moment.tzinfo is None or publication['source_url'] not in {
                    str(_news_body_mapping(row[8]).get('link') or ''),str(_news_body_mapping(row[8]).get('original_link') or '')}:
                raise NewsBodyConflict('original publication URL or timezone differs from native input')
    receipt,wake = _accept_news_job_result(db,key,result,attempts=attempts,postgres=postgres,now_provider=now_provider)
    receipt={**receipt,'job_key':key,'attempts':attempts,'owner':owner}
    if receipt['state'] in {'completed','failed','pending'}:
        pin.update(accepted_envelope_hash=accepted_hash,accepted_receipt=receipt)
        changed=_news_body_execute(db,"UPDATE central_news_jobs SET body_input_ref=? WHERE job_key=?",
            (json.dumps(pin,sort_keys=True,separators=(',',':')),key),postgres=postgres).rowcount
        if changed!=1:
            raise RuntimeError('BODY first receipt was not saved to its accepted job')
    return receipt,wake
