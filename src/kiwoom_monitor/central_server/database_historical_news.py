"""Historical news lease completion and archived market-feed batch persistence.

The store assembly supplies the existing related-article policy as a callable.
DB helpers borrow the caller cursor and preserve native transaction ownership.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from threading import Event, Thread
from time import monotonic, time
from typing import Any, Callable, Iterable, Mapping

from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION, SUPPLY_CONTRACT_RULE_VERSION, stable_document_hash,
)
from .database_codec import json_mapping
from .database_news_jobs import _news_job_rows
from .database_news_job_writes import _notify_news_job_wakeup
from .database_news_sources import _save_sqlite_news_source_page, _save_postgres_news_source_page
from .database_news_revisions import (
    _save_sqlite_news_body, _save_postgres_news_body,
    _load_sqlite_news_history, _load_postgres_news_history,
    _save_sqlite_news_event, _save_postgres_news_event,
)
from .postgres_access import _sample_postgres_backend_waits, _postgres_wait_summary

logger = logging.getLogger("kiwoom_monitor.central_server.database")


class SQLiteHistoricalNewsStoreMixin:
    def claim_external_historical_news_job(self, stage: str,
                                           excluded_codes: tuple[str, ...] = (),
                                           scope: str = "all") -> dict[str, Any] | None:
        if stage not in {"BODY", "RULE"}:
            raise ValueError("BODY 또는 RULE 작업만 외부 처리할 수 있습니다.")
        if scope not in {"all", "pc_market", "pc_search", "pc"}:
            raise ValueError("지원하지 않는 과거 뉴스 작업 범위입니다.")
        claimed_at = time()
        excluded = tuple(sorted(set(excluded_codes)))
        exclusion_sql = f" AND j.target_id NOT IN ({','.join('?' for _ in excluded)})" if excluded else ""
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT j.job_key,j.article_revision_id,j.stock_code,j.target_id,j.stage,j.input_hash,"
                "j.processing_version,j.attempts,j.payload_json,j.updated_at "
                "FROM central_news_jobs j JOIN central_news_article_revisions a "
                "ON a.article_revision_id=j.article_revision_id "
                "WHERE j.state='PENDING' AND j.stage=? AND j.processing_version=? "
                "AND j.next_retry_at<=? "
                + ("AND a.collection_scope='historical_market_pc_backfill' "
                   if scope == "pc_market" else
                   "AND a.collection_scope='historical_news_pc_backfill' "
                   if scope == "pc_search" else
                   "AND a.collection_scope IN ('historical_backfill','historical_market_backfill',"
                   "'historical_market_pc_backfill','historical_news_pc_backfill') " if scope == "pc" else
                   "AND a.collection_scope IN ('historical_backfill','historical_market_backfill',"
                   "'historical_market_pc_backfill','historical_news_pc_backfill') ")
                + exclusion_sql + " ORDER BY j.updated_at LIMIT 1",
                (stage, ARTICLE_BODY_EXTRACTOR_VERSION if stage == "BODY" else SUPPLY_CONTRACT_RULE_VERSION,
                 claimed_at, *excluded),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE central_news_jobs SET state='RUNNING',attempts=attempts+1,updated_at=? "
                "WHERE job_key=? AND state='PENDING'", (claimed_at, row[0]),
            )
        return _news_job_rows([row])[0]

    def complete_external_historical_news_job(self, value: dict[str, Any]) -> dict[str, str]:
        with self._lock, self._connection() as connection:
            result = _complete_external_news_job(connection, value, postgres=False, candidate_grouper=self._candidate_grouper)
        if value.get("stage") == "BODY":
            _notify_news_job_wakeup(self)
        return result

    def save_historical_market_news_batch(self, source: str, target_date: str,
                                          batch_id: str, items: list[dict[str, Any]],
                                          processing_owner: str = "nas") -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            prior = connection.execute("SELECT 1 FROM central_news_source_runs WHERE run_id=? LIMIT 1",
                                       (batch_id,)).fetchone()
            if prior:
                return {"state": "already_imported", "raw_count": len(items)}
            result = _save_sqlite_news_source_page(connection,
                _historical_market_source_page(source, target_date, batch_id, items, processing_owner))
        if items:
            _notify_news_job_wakeup(self)
        return {"state": "imported", **result}


class PostgresHistoricalNewsStoreMixin:
    def claim_external_historical_news_job(self, stage: str,
                                           excluded_codes: tuple[str, ...] = (),
                                           scope: str = "all") -> dict[str, Any] | None:
        if stage not in {"BODY", "RULE"}:
            raise ValueError("BODY 또는 RULE 작업만 외부 처리할 수 있습니다.")
        if scope not in {"all", "pc_market", "pc_search", "pc"}:
            raise ValueError("지원하지 않는 과거 뉴스 작업 범위입니다.")
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="news.external_claim", writer_kind="news_external_claim",
            operation="claim_external_historical_news_job", source=scope,
        )
        claimed_at = time()
        excluded = tuple(sorted(set(excluded_codes)))
        exclusion_sql = f" AND j.target_id NOT IN ({','.join('%s' for _ in excluded)})" if excluded else ""
        started_at = monotonic()
        connected_at = started_at
        selected_at = started_at
        updated_at = started_at
        connection = None
        backend_pid = 0
        wait_stop = Event()
        wait_samples: list[tuple[str, str, tuple[int, ...]]] = []
        wait_thread = None
        outcome = "no_job"
        phase = "connect"
        try:
            connection = open_observed_connection(self._connect, writer)
            connected_at = monotonic()
            phase = "select"
            backend_pid = int(getattr(getattr(connection, "info", None), "backend_pid", 0) or 0)
            if backend_pid > 0:
                wait_thread = Thread(
                    target=_sample_postgres_backend_waits,
                    args=(self._database_url, backend_pid, wait_stop, wait_samples),
                    name="news-claim-postgres-wait-probe", daemon=True,
                )
                wait_thread.start()
            with connection, connection.cursor() as cursor:
                cursor.execute(
                "SELECT j.job_key,j.article_revision_id,j.stock_code,j.target_id,j.stage,j.input_hash,"
                "j.processing_version,j.attempts,j.payload_json,j.updated_at "
                "FROM central_news_jobs j JOIN central_news_article_revisions a "
                "ON a.article_revision_id=j.article_revision_id "
                "WHERE j.state='PENDING' AND j.stage=%s AND j.processing_version=%s "
                "AND j.next_retry_at<=%s "
                + ("AND a.collection_scope='historical_market_pc_backfill' "
                   if scope == "pc_market" else
                   "AND a.collection_scope='historical_news_pc_backfill' "
                   if scope == "pc_search" else
                   "AND a.collection_scope IN ('historical_backfill','historical_market_backfill',"
                   "'historical_market_pc_backfill','historical_news_pc_backfill') " if scope == "pc" else
                   "AND a.collection_scope IN ('historical_backfill','historical_market_backfill',"
                   "'historical_market_pc_backfill','historical_news_pc_backfill') ")
                + exclusion_sql + " ORDER BY j.updated_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED",
                (stage, ARTICLE_BODY_EXTRACTOR_VERSION if stage == "BODY" else SUPPLY_CONTRACT_RULE_VERSION,
                 claimed_at, *excluded),
                )
                row = cursor.fetchone()
                selected_at = monotonic()
                phase = "update_or_commit"
                if row is None:
                    return None
                cursor.execute(
                    "UPDATE central_news_jobs SET state='RUNNING',attempts=attempts+1,updated_at=%s "
                    "WHERE job_key=%s", (claimed_at, row[0]),
                )
                updated_at = monotonic()
            outcome = "claimed"
            return _news_job_rows([row])[0]
        except BaseException as error:
            outcome = type(error).__name__
            if phase == "connect":
                connected_at = monotonic()
                selected_at = connected_at
            elif phase == "select":
                selected_at = monotonic()
            raise
        finally:
            completed_at = monotonic()
            wait_stop.set()
            if wait_thread is not None and wait_thread.is_alive():
                wait_thread.join(timeout=0.05)
            elapsed_ms = round((completed_at - started_at) * 1000)
            if elapsed_ms >= 1000 or outcome not in {"claimed", "no_job"}:
                logger.warning(
                    "slow PostgreSQL historical news claim stage=%s scope=%s outcome=%s "
                    "excluded_count=%d total_ms=%d connect_ms=%d select_ms=%d "
                    "update_commit_ms=%d backend_pid=%d waits=%s db_call_id=%s",
                    stage, scope, outcome, len(excluded), elapsed_ms,
                    round((connected_at - started_at) * 1000),
                    round((selected_at - connected_at) * 1000),
                    round((completed_at - selected_at) * 1000),
                    backend_pid,
                    _postgres_wait_summary(wait_samples),
                    writer.call_id,
                )

    def complete_external_historical_news_job(self, value: dict[str, Any]) -> dict[str, str]:
        from .postgres_access import DBWriterContext, open_observed_connection

        stage = str(value.get("stage") or "")
        writer = DBWriterContext(
            writer_family="news.external_finish",
            writer_kind=f"news_external_finish:{stage}" if stage in {"BODY", "RULE"}
            else "news_external_finish:invalid",
            operation="complete_external_historical_news_job", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            result = _complete_external_news_job(cursor, value, postgres=True, candidate_grouper=self._candidate_grouper)
        if stage == "BODY":
            _notify_news_job_wakeup(self)
        return result

    def save_historical_market_news_batch(self, source: str, target_date: str,
                                          batch_id: str, items: list[dict[str, Any]],
                                          processing_owner: str = "nas") -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="news.historical_market_batch",
            writer_kind=f"news_historical_market:{source}",
            operation="save_historical_market_news_batch",
            rows_attempted=len(items), source=processing_owner,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (batch_id,))
            cursor.execute("SELECT 1 FROM central_news_source_runs WHERE run_id=%s LIMIT 1", (batch_id,))
            if cursor.fetchone():
                return {"state": "already_imported", "raw_count": len(items)}
            result = _save_postgres_news_source_page(cursor,
                _historical_market_source_page(source, target_date, batch_id, items, processing_owner))
        if items:
            _notify_news_job_wakeup(self)
        return {"state": "imported", **result}


def _historical_market_source_page(source: str, target_date: str, batch_id: str,
                                   items: list[dict[str, Any]], processing_owner: str = "nas") -> dict[str, Any]:
    if source not in {"flash", "world"} or len(target_date) != 10 or processing_owner not in {"nas", "pc"}:
        raise ValueError("과거 시황 원천 또는 날짜가 올바르지 않습니다.")
    datetime.fromisoformat(target_date)
    now = time()
    return {
        "source_id": f"naver-stock:{source}:historical:{target_date}",
        "scope": "historical_market_pc_backfill" if processing_owner == "pc" else "historical_market_backfill",
        "query_text": source,
        "run_id": batch_id, "page_start": 1, "checked_at": now, "completed_at": now,
        "request_count": 0, "budget_remaining": 0, "coverage": "historical_market_import",
        "truncated": False, "error": "", "next_start": 1,
        "next_schedule_at": now, "last_success": now,
        "document": {"target_date": target_date, "source": source,
                     "scope_statement": "PC archived Naver Stock market feed"},
        "items": items,
    }


def _complete_external_news_job(db: Any, value: dict[str, Any], *, postgres: bool,
                                candidate_grouper: Callable[[Mapping[str, Any], Iterable[Mapping[str, Any]]], tuple[str, ...]]) -> dict[str, str]:
    """Commit a leased historical BODY/RULE result with its revision and job state."""
    marker = "%s" if postgres else "?"

    def query(sql: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        statement = sql.replace("?", marker)
        if postgres:
            db.execute(statement, params)
            return db.fetchall() if statement.lstrip().upper().startswith("SELECT") else []
        cursor = db.execute(statement, params)
        return cursor.fetchall() if statement.lstrip().upper().startswith("SELECT") else []

    job_key = str(value["job_key"])
    row = query(
        "SELECT j.article_revision_id,j.stock_code,j.target_id,j.stage,j.input_hash,"
        "j.processing_version,j.attempts,j.state,j.output_ref,j.payload_json,a.collection_scope "
        "FROM central_news_jobs j JOIN central_news_article_revisions a "
        "ON a.article_revision_id=j.article_revision_id WHERE j.job_key=?"
        + (" FOR UPDATE OF j" if postgres else ""), (job_key,),
    )
    if not row or str(row[0][10]) not in {"historical_backfill", "historical_market_backfill",
                                            "historical_market_pc_backfill", "historical_news_pc_backfill"}:
        raise ValueError("과거 뉴스 작업이 존재하지 않습니다.")
    article_id, stock_code, target_id, stage, input_hash, version, attempts, state, prior_ref, payload_raw, _ = row[0]
    if state == "COMPLETED" and int(attempts) == int(value["attempts"]):
        return {"state": "already_completed", "output_ref": str(prior_ref)}
    if state != "RUNNING" or int(attempts) != int(value["attempts"]):
        raise ValueError("뉴스 작업 소유권이 만료되었거나 다른 실행기가 완료했습니다.")
    if str(value["stage"]) != str(stage):
        raise ValueError("뉴스 작업 단계가 일치하지 않습니다.")
    payload = json_mapping(payload_raw)
    if str(value.get("error") or ""):
        new_state = "FAILED" if int(attempts) >= 3 else "PENDING"
        query(
            "UPDATE central_news_jobs SET state=?,error=?,next_retry_at=?,updated_at=? WHERE job_key=?",
            (new_state, str(value["error"])[:1000], time() + min(60.0, 2 ** min(int(attempts), 5)), time(), job_key),
        )
        return {"state": new_state.lower(), "output_ref": ""}

    if stage == "BODY":
        if str(version) != ARTICLE_BODY_EXTRACTOR_VERSION:
            raise ValueError("본문 추출기 버전이 서버와 다릅니다.")
        body_text = str(value.get("body_text") or "")
        body_status = str(value.get("body_status") or "")
        if body_status not in {"fulltext", "summary_only"} or not body_text.strip():
            raise ValueError("본문 상태 또는 내용이 올바르지 않습니다.")
        if len(body_text) > 2_000_000:
            raise ValueError("기사 본문이 허용 크기를 초과합니다.")
        article = query("SELECT content_hash FROM central_news_article_revisions WHERE article_revision_id=?", (article_id,))
        if not article or str(article[0][0]) != str(input_hash):
            raise ValueError("기사 내용 해시가 작업 입력과 다릅니다.")
        previous = query(
            "SELECT body_revision_id FROM central_news_body_revisions WHERE article_revision_id=? "
            "AND content_hash=? AND extractor_version=? AND status=? "
            "ORDER BY accepted_sequence DESC LIMIT 1",
            (article_id, stable_document_hash({"body": body_text}), version, body_status),
        )
        output_ref = str(previous[0][0]) if previous else (
            _save_postgres_news_body(db, {"article_revision_id": article_id, "extractor_version": version,
                "fetched_at": value.get("fetched_at"), "status": body_status, "body_text": body_text})
            if postgres else _save_sqlite_news_body(db, {"article_revision_id": article_id,
                "extractor_version": version, "fetched_at": value.get("fetched_at"),
                "status": body_status, "body_text": body_text})
        )
        publication = str(value.get("original_published_at") or "")
        if publication:
            document = {"published_at": publication, "source_url": str(value.get("source_url") or ""),
                        "source": "article_html", "precision": "second"}
            query(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=excluded.updated_at,document_json=excluded.document_json "
                "WHERE central_documents.document_json<>excluded.document_json"
                if not postgres else
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json "
                "WHERE central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json",
                ("news_original_publication", article_id, "published_at", time(),
                 json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
            )
    elif stage == "RULE":
        if str(version) != SUPPLY_CONTRACT_RULE_VERSION:
            raise ValueError("규칙 버전이 서버와 다릅니다.")
        body_id = str(payload.get("body_revision_id") or "")
        body = query("SELECT content_hash FROM central_news_body_revisions "
                     "WHERE body_revision_id=? AND article_revision_id=?", (body_id, article_id))
        if not body or str(body[0][0]) != str(input_hash):
            raise ValueError("본문 리비전 또는 해시가 작업 입력과 다릅니다.")
        assessment = value.get("assessment")
        sentences = value.get("core_sentences")
        if not isinstance(assessment, dict) or not isinstance(sentences, list):
            raise ValueError("규칙 평가 결과 형식이 올바르지 않습니다.")
        target_code = str(payload.get("stock_code") or target_id)
        if target_code != str(target_id):
            raise ValueError("규칙 대상 종목이 일치하지 않습니다.")
        article_row = query("SELECT published_at FROM central_news_article_revisions WHERE article_revision_id=?", (article_id,))
        publication_row = query("SELECT document_json FROM central_documents WHERE collection=? AND owner=? AND document_key=?",
                                ("news_original_publication", article_id, "published_at"))
        original_at = str(json_mapping(publication_row[0][0]).get("published_at") or "") if publication_row else ""
        listing_at = str(article_row[0][0] or "")
        document = {"article_revision_id": article_id, "body_revision_id": body_id,
                    "rule_version": "stock-news-assessment-v1", "published_at": original_at or listing_at,
                    "listing_published_at": listing_at, "original_published_at": original_at,
                    "published_at_source": "article_html" if original_at else "listing",
                    "assessment": assessment, "core_sentences": sentences}
        query(
            "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
            "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
            + ("updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json "
               "WHERE central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json" if postgres else
               "updated_at=excluded.updated_at,document_json=excluded.document_json "
               "WHERE central_documents.document_json<>excluded.document_json"),
            ("news_assessment", target_code, article_id, time(),
             json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
        )
        result = value.get("rule_result")
        if result is None:
            output_ref = f"ignored:{body_id}"
        else:
            if not isinstance(result, dict) or str(result.get("rule_version")) != str(version):
                raise ValueError("공급계약 규칙 결과 버전이 일치하지 않습니다.")
            encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            rule_hash = hashlib.sha256("\0".join((str(article_id), body_id, str(version),
                          hashlib.sha256(encoded).hexdigest())).encode("utf-8")).hexdigest()
            history = (_load_postgres_news_history(db, "article", str(stock_code), "", None, 100)
                       if postgres else _load_sqlite_news_history(db, "article", str(stock_code), "", None, 100))
            recent = [{**dict(item["document"]), "identity": item["identity"]}
                      for item in history if item["article_revision_id"] != article_id]
            current = query("SELECT identity,document_json FROM central_news_article_revisions WHERE article_revision_id=?",
                            (article_id,))[0]
            source = {**json_mapping(current[1]), "identity": str(current[0]),
                      "stock_code": target_code, "stock_name": str(payload.get("stock_name") or target_code)}
            candidates = candidate_grouper(source, recent)
            event_value = {"stock_code": target_code, "article_revision_id": article_id,
                           "body_revision_id": body_id, "rule_version": version,
                           "input_hash": rule_hash, "candidate_identities": candidates, "result": result}
            output_ref = (_save_postgres_news_event(db, event_value) if postgres
                          else _save_sqlite_news_event(db, event_value))
    else:
        raise ValueError("지원하지 않는 외부 뉴스 작업 단계입니다.")
    query("UPDATE central_news_jobs SET state='COMPLETED',output_ref=?,error='',updated_at=? WHERE job_key=?",
          (output_ref, time(), job_key))
    return {"state": "completed", "output_ref": output_ref}
