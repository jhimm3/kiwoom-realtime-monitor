"""Central document persistence and its theme/news sidecars."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from time import monotonic, time
from typing import Any

from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION,
    stable_document_hash,
)
from kiwoom_monitor.domain.research_contract import ThemeSnapshotSource

from .database_news_job_writes import (
    _insert_sqlite_news_job, _insert_postgres_news_job, _notify_news_job_wakeup,
)

from .database_codec import (
    bounded_limit,
    document_result_rows,
    document_select_query,
    document_value_rows,
)

logger = logging.getLogger(__name__)


class SQLiteDocumentStoreMixin:
    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        now = time()
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=excluded.updated_at,document_json=excluded.document_json "
                "WHERE central_documents.document_json<>excluded.document_json",
                document_value_rows(collection, values, now),
            )
            if collection == "theme_metadata":
                _append_sqlite_theme_snapshot(connection, values, received_at=now)
            elif collection == "news_article":
                _append_sqlite_news_articles(connection, values, received_at=now)
        if collection == "news_article":
            _notify_news_job_wakeup(self)

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        """컬렉션 전체를 한 트랜잭션에서 현재 스냅샷으로 교체한다."""
        now = time()
        rows = document_value_rows(collection, values, now)
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM central_documents WHERE collection=?", (collection,))
            if rows:
                connection.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(?,?,?,?,?)", rows,
                )
            if collection == "theme_metadata":
                _append_sqlite_theme_snapshot(connection, values, received_at=now)

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        sql, parameters = document_select_query(
            collection, owner, limit, offset, updated_after, placeholder="?",
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return document_result_rows(rows)

    def load_document(
        self, collection: str, owner: str, key: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT owner,document_key,updated_at,document_json "
                "FROM central_documents WHERE collection=? AND owner=? AND document_key=?",
                (collection, owner, key),
            ).fetchone()
        return document_result_rows((row,))[0] if row is not None else None

    def load_theme_snapshots(
        self, *, available_at: float | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
            "origin_device,revision_of,document_json FROM central_theme_snapshots"
        )
        parameters: list[object] = []
        if available_at is not None:
            sql += " WHERE available_at<=?"
            parameters.append(float(available_at))
        sql += " ORDER BY accepted_sequence DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 1000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return _theme_snapshot_result_rows(rows)


class PostgresDocumentStoreMixin:
    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        started_at = monotonic()
        now = time()
        writer = None
        observed_kinds = {
            "news_article": "document:news_article",
            "news_ai": "document:news_ai",
            "news_ai_shared": "document:news_ai_shared",
            "news_request_usage": "document:news_request_usage",
            "journal_news_link": "document:journal_news_link",
            "journal_v2_news_links": "document:journal_v2_news_links",
            "journal_settings": "document:journal_settings",
            "journal_fills": "document:journal_fills",
            "journal_reviews": "document:journal_reviews",
            "journal_setups": "document:journal_setups",
            "journal_cycle_overrides": "document:journal_cycle_overrides",
            "journal_group_overrides": "document:journal_group_overrides",
            "journal_entry_snapshots": "document:journal_entry_snapshots",
            "journal_costs": "document:journal_costs",
            "journal_stocks": "document:journal_stocks",
            "journal_backfill": "document:journal_backfill",
            "journal_v2_fills": "document:journal_v2_fills",
            "journal_v2_reviews": "document:journal_v2_reviews",
            "journal_v2_setups": "document:journal_v2_setups",
            "journal_v2_cycle_overrides": "document:journal_v2_cycle_overrides",
            "journal_v2_group_overrides": "document:journal_v2_group_overrides",
            "journal_v2_entry_snapshots": "document:journal_v2_entry_snapshots",
            "journal_v2_costs": "document:journal_v2_costs",
            "journal_v2_enrichment_tasks": "document:journal_v2_enrichment_tasks",
            "journal_v2_analysis_revisions": "document:journal_v2_analysis_revisions",
            "journal_v2_research_links": "document:journal_v2_research_links",
            "journal_sync_states": "document:journal_sync_states",
            "journal_v2_sync_states": "document:journal_v2_sync_states",
            "app_settings": "document:app_settings",
            "app_column_settings": "document:app_column_settings",
            "news_sync": "document:news_sync",
            "news_watchlist": "document:news_watchlist",
            "news_automation_settings": "document:news_automation_settings",
            "server_operational_settings": "document:server_operational_settings",
            "theme_profile": "document:theme_profile",
            "theme_stock": "document:theme_stock",
            "theme_metadata": "document:theme_metadata",
            "krx_trading_day_observations": "document:krx_trading_day_observations",
            "external_market_roll_state": "document:external_market_roll_state",
            "stock_catalog": "document:stock_catalog",
            "minute_trade_value_comparisons": "document:minute_trade_value_comparisons",
            "stock_nxt_eligibility": "document:stock_nxt_eligibility",
            "stock_fundamentals": "document:stock_fundamentals",
            "account_entry_symbols_daily": "document:account_entry_symbols_daily",
            "stock_price_references": "document:stock_price_references",
            "top20_daily_entrants": "document:top20_daily_entrants",
            "historical_highs": "document:historical_highs",
            "market_index_chart_coverage": "document:market_index_chart_coverage",
            "market_data_coverage_daily": "document:market_data_coverage_daily",
            "daily_bar_history_coverage": "document:daily_bar_history_coverage",
            "market_data_coverage": "document:market_data_coverage",
            "market_data_coverage_intraday": "document:market_data_coverage_intraday",
            "candidate_flow_capture": "document:candidate_flow_capture",
            "candidate_flow_finalization": "document:candidate_flow_finalization",
            "condition_search_status": "document:condition_search_status",
            "market_event_sessions": "document:market_event_sessions",
            "news_original_publication": "document:news_original_publication",
            "external_market_collection_status": "document:external_market_collection_status",
            "news_assessment": "document:news_assessment",
            "execution_forward_profiles": "document:execution_forward_profiles",
            "execution_forward_reports": "document:execution_forward_reports",
            "execution_strategy_stage_revisions": "document:execution_strategy_stage_revisions",
            "execution_feedback_evidence": "document:execution_feedback_evidence",
            "execution_feedback_reviews": "document:execution_feedback_reviews",
            "execution_feedback_improvement_proposals": (
                "document:execution_feedback_improvement_proposals"
            ),
            "execution_feedback_strategy_versions": (
                "document:execution_feedback_strategy_versions"
            ),
            "execution_feedback_revalidation_requests": (
                "document:execution_feedback_revalidation_requests"
            ),
            "execution_feedback_revalidation_receipts": (
                "document:execution_feedback_revalidation_receipts"
            ),
            "execution_mock_automation_specs": "document:execution_mock_automation_specs",
            "execution_mock_automation_admissions": (
                "document:execution_mock_automation_admissions"
            ),
            "execution_mock_automation_admission_by_spec": (
                "document:execution_mock_automation_admission_by_spec"
            ),
            "execution_mock_automation_lease_receipts": (
                "document:execution_mock_automation_lease_receipts"
            ),
            "execution_mock_automation_lease_by_admission": (
                "document:execution_mock_automation_lease_by_admission"
            ),
            "execution_mock_automation_candidate_packages": (
                "document:execution_mock_automation_candidate_packages"
            ),
            "execution_mock_automation_eligibility_policies": (
                "document:execution_mock_automation_eligibility_policies"
            ),
            "execution_mock_automation_eligibility_receipts": (
                "document:execution_mock_automation_eligibility_receipts"
            ),
            "execution_mock_automation_runner_current": (
                "document:execution_mock_automation_runner_current"
            ),
            "credential_vault_state": "document:credential_vault_state",
            "execution_mock_automation_risk_snapshots": (
                "document:execution_mock_automation_risk_snapshots"
            ),
            "execution_mock_automation_current_risk": (
                "document:execution_mock_automation_current_risk"
            ),
            "execution_mock_automation_recovery_decisions": (
                "document:execution_mock_automation_recovery_decisions"
            ),
            "execution_mock_automation_current_recovery": (
                "document:execution_mock_automation_current_recovery"
            ),
            "execution_mock_automation_decision_gates": (
                "document:execution_mock_automation_decision_gates"
            ),
            "execution_mock_automation_approved_gates": (
                "document:execution_mock_automation_approved_gates"
            ),
            "execution_mock_automation_dispatch_receipts": (
                "document:execution_mock_automation_dispatch_receipts"
            ),
            "execution_mock_automation_dispatch_by_intent": (
                "document:execution_mock_automation_dispatch_by_intent"
            ),
            "execution_mock_automation_stop_revisions": (
                "document:execution_mock_automation_stop_revisions"
            ),
            "execution_mock_automation_current_stop": (
                "document:execution_mock_automation_current_stop"
            ),
        }
        writer_kind = observed_kinds.get(collection)
        if writer_kind is not None:
            from .postgres_access import DBWriterContext, open_observed_connection

            writer = DBWriterContext(
                writer_family="document.collection", writer_kind=writer_kind,
                operation="upsert_documents", rows_attempted=len(values),
            )
            db_connection = open_observed_connection(self._connect, writer)
        else:
            db_connection = self._connect()
        document_affected_rows = None
        with db_connection as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json "
                "WHERE central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json",
                document_value_rows(collection, values, now),
            )
            if collection == "top20_daily_entrants":
                affected = getattr(cursor, "rowcount", -1)
                if affected is not None and affected >= 0:
                    document_affected_rows = int(affected)
            if collection == "theme_metadata":
                _append_postgres_theme_snapshot(cursor, values, received_at=now)
            elif collection == "news_article":
                _append_postgres_news_articles(cursor, values, received_at=now)
        if collection == "news_article":
            _notify_news_job_wakeup(self)
        from .diagnostic_metrics import record_writer_transaction
        record_writer_transaction(f"document:{collection}", len(values),
                                  round((monotonic() - started_at) * 1000),
                                  domain_counts=({"affected_rows": document_affected_rows}
                                                 if document_affected_rows is not None else None),
                                  db_call_id=writer.call_id if writer else None)

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        """컬렉션 전체를 한 트랜잭션에서 현재 스냅샷으로 교체한다."""
        now = time()
        rows = document_value_rows(collection, values, now)
        db_connection = self._connect
        if collection in {"theme_profile", "theme_stock", "theme_metadata"}:
            from .postgres_access import DBWriterContext, open_observed_connection

            writer = DBWriterContext(
                writer_family="document.collection",
                writer_kind=f"document:{collection}",
                operation="replace_documents", rows_attempted=len(values),
            )
            connection_context = open_observed_connection(db_connection, writer)
        else:
            connection_context = db_connection()
        with connection_context as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM central_documents WHERE collection=%s", (collection,))
            if rows:
                cursor.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(%s,%s,%s,%s,%s)", rows,
                )
            if collection == "theme_metadata":
                _append_postgres_theme_snapshot(cursor, values, received_at=now)

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql, parameters = document_select_query(
            collection, owner, limit, offset, updated_after, placeholder="%s",
        )
        reader = DBWriterContext(
            writer_family="read.document_collection",
            writer_kind=f"document:{collection}",
            operation="load_documents", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return document_result_rows(rows)

    def load_document(
        self, collection: str, owner: str, key: str,
    ) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.document_collection",
            writer_kind=f"document:{collection}:single",
            operation="load_document", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT owner,document_key,updated_at,document_json "
                "FROM central_documents WHERE collection=%s AND owner=%s AND document_key=%s",
                (collection, owner, key),
            )
            row = cursor.fetchone()
        return document_result_rows((row,))[0] if row is not None else None

    def load_theme_snapshots(
        self, *, available_at: float | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql = (
            "SELECT snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
            "origin_device,revision_of,document_json FROM central_theme_snapshots"
        )
        parameters: list[object] = []
        if available_at is not None:
            sql += " WHERE available_at<=%s"
            parameters.append(float(available_at))
        sql += " ORDER BY accepted_sequence DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 1000))
        reader = DBWriterContext(
            writer_family="read.theme_snapshots", writer_kind="theme_snapshots",
            operation="load_theme_snapshots", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return _theme_snapshot_result_rows(rows)




def _theme_snapshot_source(values: list[dict[str, Any]]) -> ThemeSnapshotSource | None:
    full = next(
        (
            value for value in reversed(values)
            if str(value.get("owner", "")) == "default"
            and str(value.get("key", "")) == "full"
        ),
        None,
    )
    if full is None:
        return None
    document = full.get("document")
    if not isinstance(document, dict):
        raise ValueError("테마 이력 원본 문서는 JSON 객체여야 합니다.")
    return ThemeSnapshotSource.from_document(
        document,
        effective_at=full.get("effective_at"),
        origin_device=full.get("origin_device"),
    )


def _append_sqlite_theme_snapshot(
    connection: sqlite3.Connection, values: list[dict[str, Any]], *, received_at: float,
) -> None:
    source = _theme_snapshot_source(values)
    if source is None:
        return
    previous = connection.execute(
        "SELECT snapshot_id,content_hash FROM central_theme_snapshots "
        "ORDER BY accepted_sequence DESC LIMIT 1"
    ).fetchone()
    if previous is not None and str(previous[1]) == source.content_hash:
        return
    connection.execute(
        "INSERT INTO central_theme_snapshots("
        "snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
        "origin_device,revision_of,document_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex, source.profile_id, source.content_hash, source.effective_at,
            received_at, time(), source.origin_device,
            str(previous[0]) if previous is not None else None,
            json.dumps(source.document, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def _append_postgres_theme_snapshot(
    cursor: Any, values: list[dict[str, Any]], *, received_at: float,
) -> None:
    source = _theme_snapshot_source(values)
    if source is None:
        return
    # 동시에 도착한 여러 PC의 같은 문서도 직전 hash를 한 순서로 비교한다.
    cursor.execute("LOCK TABLE central_theme_snapshots IN SHARE ROW EXCLUSIVE MODE")
    cursor.execute(
        "SELECT snapshot_id,content_hash FROM central_theme_snapshots "
        "ORDER BY accepted_sequence DESC LIMIT 1"
    )
    previous = cursor.fetchone()
    if previous is not None and str(previous[1]) == source.content_hash:
        return
    cursor.execute(
        "INSERT INTO central_theme_snapshots("
        "snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
        "origin_device,revision_of,document_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid.uuid4().hex, source.profile_id, source.content_hash, source.effective_at,
            received_at, time(), source.origin_device,
            str(previous[0]) if previous is not None else None,
            json.dumps(source.document, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def _theme_snapshot_result_rows(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    keys = (
        "snapshot_id", "profile_id", "content_hash", "effective_at", "received_at",
        "available_at", "origin_device", "revision_of",
    )
    return [
        {
            **dict(zip(keys, row[:8], strict=True)),
            "document": row[8] if isinstance(row[8], dict) else json.loads(str(row[8])),
        }
        for row in rows
    ]


def _article_source(value: dict[str, Any]) -> tuple[str, str, dict[str, Any], str, str]:
    document = value.get("document")
    if not isinstance(document, dict):
        raise ValueError("뉴스 기사 문서는 JSON 객체여야 합니다.")
    stock_code = str(document.get("stock_code") or value.get("owner") or "").strip()
    identity = str(document.get("identity") or value.get("key") or "").strip()
    if not stock_code or not identity:
        raise ValueError("뉴스 기사에는 종목코드와 identity가 필요합니다.")
    return (
        stock_code, identity, dict(document),
        str(value.get("collector_id") or "unknown").strip() or "unknown",
        str(value.get("collection_scope") or "watchlist").strip() or "watchlist",
    )


def _append_sqlite_news_articles(
    connection: sqlite3.Connection, values: list[dict[str, Any]], *, received_at: float,
) -> None:
    for value in values:
        stock, identity, document, collector, scope = _article_source(value)
        content_hash = stable_document_hash(document)
        previous = connection.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=? AND identity=? ORDER BY accepted_sequence DESC LIMIT 1",
            (stock, identity),
        ).fetchone()
        same_source = connection.execute(
            "SELECT 1 FROM central_news_article_revisions WHERE stock_code=? AND identity=? "
            "AND collector_id=? AND content_hash=? LIMIT 1",
            (stock, identity, collector, content_hash),
        ).fetchone()
        if same_source is not None:
            continue
        revision_id, available_at = uuid.uuid4().hex, time()
        connection.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
            "received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (revision_id, stock, identity, content_hash, collector, document.get("published_at"),
             received_at, available_at, scope, str(previous[0]) if previous else None,
             json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
        )
        _insert_sqlite_news_job(
            connection, revision_id, stock, stock, "BODY", content_hash,
            ARTICLE_BODY_EXTRACTOR_VERSION, document, available_at,
        )


def _append_postgres_news_articles(cursor: Any, values: list[dict[str, Any]], *, received_at: float) -> None:
    cursor.execute("LOCK TABLE central_news_article_revisions IN SHARE ROW EXCLUSIVE MODE")
    for value in values:
        stock, identity, document, collector, scope = _article_source(value)
        content_hash = stable_document_hash(document)
        cursor.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=%s AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1",
            (stock, identity),
        )
        previous = cursor.fetchone()
        cursor.execute(
            "SELECT 1 FROM central_news_article_revisions WHERE stock_code=%s AND identity=%s "
            "AND collector_id=%s AND content_hash=%s LIMIT 1",
            (stock, identity, collector, content_hash),
        )
        if cursor.fetchone() is not None:
            continue
        revision_id, available_at = uuid.uuid4().hex, time()
        cursor.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
            "received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (revision_id, stock, identity, content_hash, collector, document.get("published_at"),
             received_at, available_at, scope, str(previous[0]) if previous else None,
             json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
        )
        _insert_postgres_news_job(
            cursor, revision_id, stock, stock, "BODY", content_hash,
            ARTICLE_BODY_EXTRACTOR_VERSION, document, available_at,
        )
