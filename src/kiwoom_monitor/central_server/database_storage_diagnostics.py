"""Read-only storage diagnostics for the existing QueryStore backends."""
from __future__ import annotations

import sqlite3
from pathlib import Path


_STORAGE_CATEGORY_LABELS = {
    "news": "뉴스",
    "market": "주식 누적자료",
    "research": "연구·시뮬레이션",
    "account": "계좌·주문",
    "other": "설정·기타",
}


def _storage_category(name: str, *, collection: bool = False) -> str:
    normalized = str(name).strip().lower()
    if (
        normalized.startswith("news_")
        if collection
        else normalized.startswith("central_news_")
    ):
        return "news"
    if collection and normalized.startswith(("journal_", "execution_")):
        return "account"
    if collection and normalized.startswith(("research_", "shadow_")):
        return "research"
    if collection and normalized in {
        "stock_fundamentals", "stock_nxt_eligibility", "historical_highs",
        "top20_daily_entrants", "candidate_flow_capture", "candidate_flow_finalization",
        "market_data_coverage", "market_data_coverage_daily", "market_data_coverage_intraday",
        "daily_bar_history_coverage",
        "market_index_chart_coverage", "condition_search_status", "market_event_sessions",
    }:
        return "market"
    if not collection and normalized.startswith(("central_account_", "central_execution_")):
        return "account"
    if not collection and normalized.startswith(("central_research_", "central_shadow_")):
        return "research"
    if not collection and normalized in {
        "central_second_trade_bars", "central_minute_bars", "central_five_minute_bars", "central_daily_bars",
        "central_dataset_snapshots", "central_realtime_latest", "central_external_bars",
        "central_market_data_observation_meta", "central_observation_revisions",
        "central_minute_bar_operations", "central_hot_cohort_current",
        "central_hot_cohort_revisions", "central_upper_limit_fact_revisions",
        "central_vi_event_revisions",
    }:
        return "market"
    return "other"


def _storage_breakdown_rows(
    table_stats: list[tuple[str, int, int]],
    shared_documents: list[tuple[object, object, object]],
) -> list[dict[str, object]]:
    totals = {
        key: {"category": key, "label": label, "estimated_bytes": 0, "rows": 0, "tables": 0}
        for key, label in _STORAGE_CATEGORY_LABELS.items()
    }
    shared_table_bytes = 0
    for table, rows, size in table_stats:
        if table == "central_documents":
            shared_table_bytes = max(0, int(size))
            continue
        category = _storage_category(table)
        totals[category]["estimated_bytes"] += max(0, int(size))
        totals[category]["rows"] += max(0, int(rows))
        totals[category]["tables"] += 1

    documents = []
    logical_total = 0
    for collection, rows, logical_bytes in shared_documents:
        category = _storage_category(str(collection), collection=True)
        count = max(0, int(rows))
        amount = max(0, int(logical_bytes))
        documents.append((category, count, amount))
        logical_total += amount
    allocated = 0
    document_categories: set[str] = set()
    for index, (category, count, amount) in enumerate(documents):
        document_categories.add(category)
        if shared_table_bytes and logical_total:
            share = (
                shared_table_bytes - allocated
                if index == len(documents) - 1
                else round(shared_table_bytes * amount / logical_total)
            )
            allocated += share
            totals[category]["estimated_bytes"] += max(0, share)
        totals[category]["rows"] += count
    if shared_table_bytes and not logical_total:
        totals["other"]["estimated_bytes"] += shared_table_bytes
    for category in document_categories:
        totals[category]["tables"] += 1
    return [totals[key] for key in ("news", "market", "research", "account", "other")]


class SQLiteStorageDiagnosticsStoreMixin:
    def storage_size_bytes(self) -> int | None:
        if self._memory_uri:
            return None
        try:
            return self._path.stat().st_size
        except OSError:
            return None

    def storage_breakdown(self) -> list[dict[str, object]]:
        with self._lock, self._connection() as connection:
            tables = [str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'central_%'"
            ).fetchall()]
            stats: list[tuple[str, int, int]] = []
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                rows = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
                try:
                    size_row = connection.execute(
                        "SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name=? OR name IN ("
                        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?)",
                        (table, table),
                    ).fetchone()
                    size = int(size_row[0]) if size_row else 0
                except sqlite3.DatabaseError:
                    size = 0
                stats.append((table, rows, size))
            shared = connection.execute(
                "SELECT collection,COUNT(*),COALESCE(SUM(length(document_json)+length(collection)+"
                "length(owner)+length(document_key)),0) FROM central_documents GROUP BY collection"
            ).fetchall() if "central_documents" in tables else []
        return _storage_breakdown_rows(stats, shared)


class PostgresStorageDiagnosticsStoreMixin:
    def storage_size_bytes(self) -> int | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.diagnostics", writer_kind="database_size",
            operation="storage_size_bytes", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_database_size(current_database())")
            row = cursor.fetchone()
        return int(row[0]) if row else None

    def storage_breakdown(self) -> list[dict[str, object]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.diagnostics", writer_kind="storage_breakdown",
            operation="storage_breakdown", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname,COALESCE(n_live_tup,0)::bigint,"
                "pg_total_relation_size(relid)::bigint "
                "FROM pg_stat_user_tables WHERE schemaname=current_schema() "
                "AND relname LIKE 'central_%' ORDER BY relname"
            )
            stats = [(str(row[0]), int(row[1]), int(row[2])) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT collection,COUNT(*)::bigint,COALESCE(SUM(pg_column_size(document_json)+"
                "octet_length(collection)+octet_length(owner)+octet_length(document_key)),0)::bigint "
                "FROM central_documents GROUP BY collection"
            )
            shared = [(str(row[0]), int(row[1]), int(row[2])) for row in cursor.fetchall()]
        return _storage_breakdown_rows(stats, shared)
