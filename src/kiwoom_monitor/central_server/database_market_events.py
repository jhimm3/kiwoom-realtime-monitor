"""Market-event history and current cohort persistence; callers own transactions."""
from __future__ import annotations

from typing import Any

from .database_codec import _event_document, bounded_limit, json_mapping


class SQLiteMarketEventStoreMixin:
    def append_vi_events(self, values: list[dict[str, Any]]) -> int:
        inserted = 0
        with self._lock, self._connection() as connection:
            for value in values:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO central_vi_event_revisions("
                    "event_id,event_key,stock_code,event_kind,vi_type,effective_at,received_at,available_at,"
                    "price,direction,trigger_count,exchange,source,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    _vi_event_values(value),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    def record_hot_cohort_revision(self, value: dict[str, Any],
                                   current: dict[str, Any] | None = None) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO central_hot_cohort_revisions("
                "revision_id,revision_key,stock_code,event_type,condition_name,condition_seq,session_id,"
                "effective_at,available_at,document_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                _cohort_revision_values(value),
            )
            if current is not None:
                connection.execute(
                    "INSERT INTO central_hot_cohort_current(stock_code,stock_name,condition_name,first_seen_at,"
                    "entry_session,last_signal,last_signal_at,active,nxt_eligible,expired_at,document_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(stock_code) DO UPDATE SET "
                    "stock_name=excluded.stock_name,condition_name=excluded.condition_name,"
                    "first_seen_at=excluded.first_seen_at,entry_session=excluded.entry_session,"
                    "last_signal=excluded.last_signal,last_signal_at=excluded.last_signal_at,active=excluded.active,"
                    "nxt_eligible=COALESCE(excluded.nxt_eligible,central_hot_cohort_current.nxt_eligible),"
                    "expired_at=excluded.expired_at,document_json=excluded.document_json",
                    _cohort_current_values(current),
                )
            return cursor.rowcount > 0

    def load_hot_cohort(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        sql = ("SELECT stock_code,stock_name,condition_name,first_seen_at,entry_session,last_signal,"
               "last_signal_at,active,nxt_eligible,expired_at,document_json FROM central_hot_cohort_current")
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY first_seen_at,stock_code"
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql).fetchall()
        return _cohort_current_rows(rows)

    def append_upper_limit_facts(self, values: list[dict[str, Any]]) -> int:
        inserted = 0
        with self._lock, self._connection() as connection:
            for value in values:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO central_upper_limit_fact_revisions("
                    "fact_id,fact_key,stock_code,session_id,status,upper_limit_price,current_price,high_price,"
                    "effective_at,available_at,source,evidence,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    _upper_limit_values(value),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    def load_market_event_history(self, kind: str, *, code: str = "",
                                  limit: int = 100) -> list[dict[str, Any]]:
        table, code_column = _market_event_table(kind)
        sql = f"SELECT document_json FROM {table}"
        parameters: list[object] = []
        if code:
            sql += f" WHERE {code_column}=?"
            parameters.append(code)
        sql += " ORDER BY accepted_sequence DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 1000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [json_mapping(row[0]) for row in rows]


class PostgresMarketEventStoreMixin:
    def append_vi_events(self, values: list[dict[str, Any]]) -> int:
        from .postgres_access import DBWriterContext, open_observed_connection

        inserted = 0
        writer = DBWriterContext(
            writer_family="market_event.revision", writer_kind="market_event:vi",
            operation="append_vi_events", rows_attempted=len(values),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            for value in values:
                cursor.execute(
                    "INSERT INTO central_vi_event_revisions(event_id,event_key,stock_code,event_kind,vi_type,"
                    "effective_at,received_at,available_at,price,direction,trigger_count,exchange,source,document_json) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    _vi_event_values(value),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    def record_hot_cohort_revision(self, value: dict[str, Any],
                                   current: dict[str, Any] | None = None) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="market_event.revision", writer_kind="market_event:hot_cohort",
            operation="record_hot_cohort_revision",
            rows_attempted=1 + int(current is not None),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_hot_cohort_revisions(revision_id,revision_key,stock_code,event_type,"
                "condition_name,condition_seq,session_id,effective_at,available_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(revision_key) DO NOTHING",
                _cohort_revision_values(value),
            )
            inserted = cursor.rowcount > 0
            if current is not None:
                cursor.execute(
                    "INSERT INTO central_hot_cohort_current(stock_code,stock_name,condition_name,first_seen_at,"
                    "entry_session,last_signal,last_signal_at,active,nxt_eligible,expired_at,document_json) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(stock_code) DO UPDATE SET "
                    "stock_name=EXCLUDED.stock_name,condition_name=EXCLUDED.condition_name,"
                    "first_seen_at=EXCLUDED.first_seen_at,entry_session=EXCLUDED.entry_session,"
                    "last_signal=EXCLUDED.last_signal,last_signal_at=EXCLUDED.last_signal_at,active=EXCLUDED.active,"
                    "nxt_eligible=COALESCE(EXCLUDED.nxt_eligible,central_hot_cohort_current.nxt_eligible),"
                    "expired_at=EXCLUDED.expired_at,document_json=EXCLUDED.document_json",
                    _cohort_current_values(current),
                )
            return inserted

    def load_hot_cohort(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql = ("SELECT stock_code,stock_name,condition_name,first_seen_at,entry_session,last_signal,"
               "last_signal_at,active,nxt_eligible,expired_at,document_json FROM central_hot_cohort_current")
        if active_only:
            sql += " WHERE active=TRUE"
        sql += " ORDER BY first_seen_at,stock_code"
        reader = DBWriterContext(
            writer_family="read.market_events", writer_kind="hot_cohort",
            operation="load_hot_cohort", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return _cohort_current_rows(rows)

    def append_upper_limit_facts(self, values: list[dict[str, Any]]) -> int:
        from .postgres_access import DBWriterContext, open_observed_connection

        inserted = 0
        writer = DBWriterContext(
            writer_family="market_event.revision", writer_kind="market_event:upper_limit",
            operation="append_upper_limit_facts", rows_attempted=len(values),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            for value in values:
                cursor.execute(
                    "INSERT INTO central_upper_limit_fact_revisions(fact_id,fact_key,stock_code,session_id,status,"
                    "upper_limit_price,current_price,high_price,effective_at,available_at,source,evidence,document_json) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(fact_key) DO NOTHING",
                    _upper_limit_values(value),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    def load_market_event_history(self, kind: str, *, code: str = "",
                                  limit: int = 100) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        table, code_column = _market_event_table(kind)
        sql = f"SELECT document_json FROM {table}"
        parameters: list[object] = []
        if code:
            sql += f" WHERE {code_column}=%s"
            parameters.append(code)
        sql += " ORDER BY accepted_sequence DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 1000))
        reader = DBWriterContext(
            writer_family="read.market_events", writer_kind=f"history:{kind}",
            operation="load_market_event_history", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return [json_mapping(row[0]) for row in rows]


def _vi_event_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["event_id"]), str(value["event_key"]), str(value["stock_code"]),
        str(value["event_kind"]), str(value["vi_type"]), value.get("effective_at"),
        float(value["received_at"]), float(value["available_at"]), value.get("price"),
        str(value.get("direction", "")), value.get("trigger_count"),
        str(value.get("exchange", "")), str(value.get("source", "")), _event_document(value),
    )


def _cohort_revision_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["revision_id"]), str(value["revision_key"]), str(value.get("stock_code", "")),
        str(value["event_type"]), str(value.get("condition_name", "")),
        str(value.get("condition_seq", "")), str(value["session_id"]),
        float(value["effective_at"]), float(value["available_at"]), _event_document(value),
    )


def _cohort_current_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["stock_code"]), str(value.get("stock_name", "")),
        str(value.get("condition_name", "")), float(value["first_seen_at"]),
        str(value["entry_session"]), str(value.get("last_signal", "")),
        float(value["last_signal_at"]), bool(value.get("active", True)),
        value.get("nxt_eligible"), value.get("expired_at"), _event_document(value),
    )


def _cohort_current_rows(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        document = json_mapping(row[10])
        document.update({
            "stock_code": str(row[0]), "stock_name": str(row[1]), "condition_name": str(row[2]),
            "first_seen_at": float(row[3]), "entry_session": str(row[4]), "last_signal": str(row[5]),
            "last_signal_at": float(row[6]), "active": bool(row[7]),
            "nxt_eligible": None if row[8] is None else bool(row[8]),
            "expired_at": None if row[9] is None else float(row[9]),
        })
        result.append(document)
    return result


def _upper_limit_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["fact_id"]), str(value["fact_key"]), str(value["stock_code"]),
        str(value["session_id"]), str(value["status"]), value.get("upper_limit_price"),
        value.get("current_price"), value.get("high_price"), float(value["effective_at"]),
        float(value["available_at"]), str(value.get("source", "")),
        str(value.get("evidence", "")), _event_document(value),
    )


def _market_event_table(kind: str) -> tuple[str, str]:
    mapping = {
        "vi": ("central_vi_event_revisions", "stock_code"),
        "cohort": ("central_hot_cohort_revisions", "stock_code"),
        "upper_limit": ("central_upper_limit_fact_revisions", "stock_code"),
    }
    if kind not in mapping:
        raise ValueError(f"지원하지 않는 시장 이벤트 종류입니다: {kind}")
    return mapping[kind]
