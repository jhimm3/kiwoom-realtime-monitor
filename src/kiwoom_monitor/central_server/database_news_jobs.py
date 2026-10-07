"""News queue ownership, claim diagnostics and request-budget persistence."""
from __future__ import annotations

import json
import sqlite3
from time import monotonic, time
from typing import Any, Callable

from kiwoom_monitor.domain.news_observation import news_job_key
from .database_codec import bounded_limit
from .database_news_job_writes import (
    _insert_sqlite_news_job, _insert_postgres_news_job, _notify_news_job_wakeup,
)
from .postgres_access import _NEWS_CLAIM_WAIT_PROBE_SLOTS, _execute_with_postgres_wait_probe


_NEWS_JOB_CLAIM_READY_SQL = (
    "SELECT job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
    "processing_version,attempts,payload_json,updated_at FROM central_news_jobs "
    "WHERE state='PENDING' AND next_retry_at<=%s "
)
_NEWS_JOB_CLAIM_HISTORICAL_SCOPES_SQL = (
    "'historical_backfill','historical_market_backfill',"
    "'historical_market_pc_backfill','historical_news_pc_backfill'"
)
_NEWS_JOB_CLAIM_ORDER_AND_LOCK_SQL = (
    "ORDER BY CASE WHEN %s<>'' AND stage=%s THEN -1 "
    "WHEN %s<>'' AND (stock_code=%s OR target_id=%s) AND stage='BODY' THEN 0 "
    "WHEN %s<>'' AND (stock_code=%s OR target_id=%s) THEN 1 "
    "WHEN stage='BODY' AND stock_code<>'GLOBAL' THEN 2 "
    "WHEN stage='BODY' THEN 3 WHEN stage='AI' THEN 4 ELSE 5 END,"
    "CASE WHEN stage='BODY' THEN -updated_at ELSE updated_at END "
    "FOR UPDATE SKIP LOCKED LIMIT %s"
)
_NEWS_JOB_CLAIM_SELECT_SQL = (
    _NEWS_JOB_CLAIM_READY_SQL
    + "AND (stage NOT IN ('BODY','RULE') OR NOT EXISTS ("
    "SELECT 1 FROM central_news_article_revisions a "
    "WHERE a.article_revision_id=central_news_jobs.article_revision_id "
    + f"AND a.collection_scope IN ({_NEWS_JOB_CLAIM_HISTORICAL_SCOPES_SQL}))) "
    + _NEWS_JOB_CLAIM_ORDER_AND_LOCK_SQL
)
_NEWS_JOB_CLAIM_DIAGNOSTIC_CANDIDATE_SQL = (
    _NEWS_JOB_CLAIM_READY_SQL
    + "AND NOT EXISTS (SELECT 1 FROM central_news_article_revisions a "
    "WHERE a.article_revision_id=central_news_jobs.article_revision_id "
    "AND central_news_jobs.stage IN ('BODY','RULE') "
    + f"AND a.collection_scope IN ({_NEWS_JOB_CLAIM_HISTORICAL_SCOPES_SQL})) "
    + _NEWS_JOB_CLAIM_ORDER_AND_LOCK_SQL
)
_NEWS_JOB_CLAIM_READ_ONLY_SQL = _NEWS_JOB_CLAIM_SELECT_SQL.replace(
    "FOR UPDATE SKIP LOCKED LIMIT %s", "LIMIT %s",
)


class SQLiteNewsJobStoreMixin:
    def set_news_job_wakeup(self, callback: Callable[[], None] | None) -> None:
        self._news_job_wakeup = callback

    def enqueue_news_ai_jobs(self, values: list[dict[str, Any]]) -> int:
        with self._lock, self._connection() as connection:
            count = _enqueue_sqlite_news_ai_jobs(connection, values)
        if count:
            _notify_news_job_wakeup(self)
        return count

    def claim_news_jobs(self, *, limit: int = 1, now: float | None = None,
                        priority_stock_code: str = "", preferred_stage: str = "") -> list[dict[str, Any]]:
        claimed_at = float(now if now is not None else time())
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE central_news_jobs SET state='PENDING',updated_at=? "
                "WHERE state='RUNNING' AND updated_at<?",
                (claimed_at, claimed_at - 120.0),
            )
            priority = str(priority_stock_code or "").strip()
            rows = connection.execute(
                "SELECT job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
                "processing_version,attempts,payload_json,updated_at FROM central_news_jobs "
                "WHERE state='PENDING' AND next_retry_at<=? "
                "AND (stage NOT IN ('BODY','RULE') OR NOT EXISTS ("
                "SELECT 1 FROM central_news_article_revisions a "
                "WHERE a.article_revision_id=central_news_jobs.article_revision_id "
                "AND a.collection_scope IN ('historical_backfill','historical_market_backfill',"
                "'historical_market_pc_backfill','historical_news_pc_backfill'))) "
                "ORDER BY CASE WHEN ?<>'' AND stage=? THEN -1 "
                "WHEN ?<>'' AND (stock_code=? OR target_id=?) AND stage='BODY' THEN 0 "
                "WHEN ?<>'' AND (stock_code=? OR target_id=?) THEN 1 "
                "WHEN stage='BODY' AND stock_code<>'GLOBAL' THEN 2 "
                "WHEN stage='BODY' THEN 3 WHEN stage='AI' THEN 4 ELSE 5 END,"
                "CASE WHEN stage='BODY' THEN -updated_at ELSE updated_at END LIMIT ?",
                (claimed_at, preferred_stage, preferred_stage,
                 priority, priority, priority, priority, priority, priority,
                 bounded_limit(limit, 4)),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE central_news_jobs SET state='RUNNING',attempts=attempts+1,updated_at=? "
                    "WHERE job_key=?", (claimed_at, row[0]),
                )
        return _news_job_rows(rows)

    def finish_news_job(self, job_key: str, output_ref: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE central_news_jobs SET state='COMPLETED',output_ref=?,error='',updated_at=? "
                "WHERE job_key=?", (output_ref, time(), job_key),
            )

    def retry_news_job(self, job_key: str, error: str, next_retry_at: float,
                       output_ref: str = "") -> None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT attempts FROM central_news_jobs WHERE job_key=?", (job_key,),
            ).fetchone()
            state = "FAILED" if row is not None and int(row[0]) >= 3 else "PENDING"
            connection.execute(
                "UPDATE central_news_jobs SET state=?,error=?,next_retry_at=?,output_ref=?,updated_at=? "
                "WHERE job_key=?",
                (state, error[:1000], float(next_retry_at), output_ref, time(), job_key),
            )
        if state == "PENDING":
            _notify_news_job_wakeup(self)

    def claim_news_request(self, scope: str, *, scope_limit: int, hard_limit: int,
                           budget_date: str) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            total = connection.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM central_news_request_budget WHERE budget_date=?",
                (budget_date,),
            ).fetchone()
            current = connection.execute(
                "SELECT request_count FROM central_news_request_budget WHERE budget_date=? AND scope=?",
                (budget_date, scope),
            ).fetchone()
            if int(total[0]) >= int(hard_limit) or (current and int(current[0]) >= int(scope_limit)):
                return False
            connection.execute(
                "INSERT INTO central_news_request_budget(budget_date,scope,request_count,updated_at) "
                "VALUES(?,?,1,?) ON CONFLICT(budget_date,scope) DO UPDATE SET "
                "request_count=central_news_request_budget.request_count+1,updated_at=excluded.updated_at",
                (budget_date, scope, time()),
            )
            return True

    def news_request_count(self, budget_date: str) -> int:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM central_news_request_budget WHERE budget_date=?",
                (budget_date,),
            ).fetchone()
        return int(row[0]) if row else 0


class PostgresNewsJobStoreMixin:
    def set_news_job_wakeup(self, callback: Callable[[], None] | None) -> None:
        self._news_job_wakeup = callback

    def enqueue_news_ai_jobs(self, values: list[dict[str, Any]]) -> int:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="news.job_enqueue", writer_kind="news_ai_job_enqueue",
            operation="enqueue_news_ai_jobs", rows_attempted=len(values),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            count = _enqueue_postgres_news_ai_jobs(cursor, values)
        if count:
            _notify_news_job_wakeup(self)
        return count

    def claim_news_jobs(self, *, limit: int = 1, now: float | None = None,
                        priority_stock_code: str = "", preferred_stage: str = "") -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        claimed_at = float(now if now is not None else time())
        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="news.job_claim", writer_kind="news_job_claim",
            operation="claim_news_jobs",
        )
        phase_ms: dict[str, float] = {}
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            def run_phase(name: str, operation: Callable[[], object]) -> object:
                if not connection.diagnostic_capture_active():
                    return operation()

                def record_phase(record: dict[str, object]) -> None:
                    phase_ms[name] = phase_ms.get(name, 0.0) + float(record["duration_ms"])
                    record["rowcount"] = getattr(cursor, "rowcount", None)
                    connection.record_phase_diagnostic(name, record)

                result, _ = _execute_with_postgres_wait_probe(
                    operation, self._database_url, connection.backend_pid or 0, True,
                    record_callback=record_phase, max_retained_samples=256,
                    capture_guard=connection.diagnostic_capture_active,
                    probe_slots=_NEWS_CLAIM_WAIT_PROBE_SLOTS,
                )
                return result

            run_phase("recover_stale", lambda: cursor.execute(
                "UPDATE central_news_jobs SET state='PENDING',updated_at=%s "
                "WHERE state='RUNNING' AND updated_at<%s", (claimed_at, claimed_at - 120.0),
            ))
            priority = str(priority_stock_code or "").strip()

            def select_candidates():
                cursor.execute(
                    _NEWS_JOB_CLAIM_SELECT_SQL,
                    (claimed_at, preferred_stage, preferred_stage,
                     priority, priority, priority, priority, priority, priority,
                     bounded_limit(limit, 4)),
                )
                return cursor.fetchall()

            rows = run_phase("select_candidates", select_candidates)
            for row in rows:
                run_phase("mark_running", lambda: cursor.execute(
                    "UPDATE central_news_jobs SET state='RUNNING',attempts=attempts+1,updated_at=%s "
                    "WHERE job_key=%s", (claimed_at, row[0]),
                ))
        from .diagnostic_metrics import record_writer_transaction
        record_writer_transaction("news_job_claim", len(rows),
                                  round((monotonic() - started_at) * 1000),
                                  db_call_id=writer.call_id, domain_phase_ms=phase_ms)
        return _news_job_rows(rows)

    def explain_news_job_claim_plan(self) -> dict[str, Any]:
        """Return bounded plan-only diagnostics for the news job claim SQL."""
        from .postgres_access import DBWriterContext, open_observed_connection

        def plan_summary(raw_plan: Any) -> list[dict[str, Any]]:
            """Return a bounded, connected view of planner nodes without row data."""
            if not isinstance(raw_plan, list) or not raw_plan:
                return []
            root = raw_plan[0].get("Plan", {}) if isinstance(raw_plan[0], dict) else {}
            nodes: list[dict[str, Any]] = []
            pending = [(root, None, 0)] if isinstance(root, dict) else []
            while pending and len(nodes) < 256:
                node, parent_node, depth = pending.pop(0)
                node_index = len(nodes)
                summary = {key: node[key] for key in (
                    "Node Type", "Parent Relationship", "Subplan Name", "Join Type",
                    "Relation Name", "Index Name", "Index Cond", "Filter", "Hash Cond",
                    "Merge Cond", "Sort Key", "Startup Cost", "Total Cost", "Plan Rows",
                    "Plan Width",
                ) if key in node}
                summary["parent_node"] = parent_node
                summary["depth"] = depth
                nodes.append(summary)
                children = node.get("Plans", ())
                if isinstance(children, list):
                    pending.extend((child, node_index, depth + 1)
                                   for child in children if isinstance(child, dict))
            if pending and nodes:
                nodes[-1]["children_truncated"] = True
            return nodes

        now_epoch = time()
        reader = DBWriterContext(
            writer_family="diagnostic.query_plan", writer_kind="news_job_claim_plan",
            operation="explain_news_job_claim_plan", access_mode="read",
        )
        plans: list[dict[str, Any]] = []
        candidate_plans: list[dict[str, Any]] = []
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT current_database(),current_setting('server_version')")
            database_name, server_version = cursor.fetchone()
            cursor.execute(
                "EXPLAIN (FORMAT JSON) UPDATE central_news_jobs "
                "SET state='PENDING',updated_at=%s "
                "WHERE state='RUNNING' AND updated_at<%s",
                (now_epoch, now_epoch - 120.0),
            )
            plans.append({"operation": "recover_stale_running", "nodes": plan_summary(cursor.fetchone()[0])})
            for stage in ("BODY", "RULE"):
                priority = ""
                parameters = (now_epoch, stage, stage, priority, priority, priority,
                              priority, priority, priority, 1)
                cursor.execute(
                    "EXPLAIN (FORMAT JSON) " + _NEWS_JOB_CLAIM_SELECT_SQL,
                    parameters,
                )
                plans.append({"operation": "claim_candidate_select", "preferred_stage": stage,
                              "nodes": plan_summary(cursor.fetchone()[0])})
                cursor.execute(
                    "EXPLAIN (FORMAT JSON) " + _NEWS_JOB_CLAIM_DIAGNOSTIC_CANDIDATE_SQL,
                    parameters,
                )
                candidate_plans.append({"operation": "claim_candidate_select", "preferred_stage": stage,
                                        "nodes": plan_summary(cursor.fetchone()[0])})
        return {"mode": "read_only_explain_without_analyze", "database": database_name,
                "postgresql": server_version, "plans": plans,
                "candidate_plans": candidate_plans}

    def analyze_news_job_claim_read_only(self, stage: str) -> dict[str, Any]:
        """Measure the claim filter/order without executing its row lock or updates."""
        from .postgres_access import DBWriterContext, open_observed_connection

        if stage not in {"BODY", "RULE"}:
            raise ValueError("BODY or RULE stage is required")
        reader = DBWriterContext(
            writer_family="diagnostic.query_plan", writer_kind="news_job_claim_read_only",
            operation="analyze_news_job_claim_read_only", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SET LOCAL statement_timeout TO '2000ms'")
            cursor.execute(
                "EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) "
                + _NEWS_JOB_CLAIM_READ_ONLY_SQL,
                (time(), stage, stage, "", "", "", "", "", "", 1),
            )
            report = cursor.fetchone()[0][0]
        root = report.get("Plan", {})
        nodes: list[dict[str, Any]] = []
        pending = [(root, None, 0)] if isinstance(root, dict) else []
        while pending and len(nodes) < 64:
            node, parent, depth = pending.pop(0)
            index = len(nodes)
            summary = {key: node[key] for key in (
                "Node Type", "Parent Relationship", "Subplan Name", "Relation Name",
                "Index Name", "Plan Rows", "Actual Rows", "Actual Loops", "Sort Method",
                "Shared Hit Blocks", "Shared Read Blocks", "Shared Dirtied Blocks",
                "Shared Written Blocks", "Temp Read Blocks", "Temp Written Blocks",
            ) if key in node}
            summary.update(parent_node=parent, depth=depth)
            nodes.append(summary)
            children = node.get("Plans", ())
            if isinstance(children, list):
                pending.extend((child, index, depth + 1)
                               for child in children if isinstance(child, dict))
        return {
            "mode": "read_only_analyze_without_row_lock",
            "preferred_stage": stage,
            "execution_ms": report.get("Execution Time"),
            "planning_ms": report.get("Planning Time"),
            "nodes": nodes,
            "truncated": bool(pending),
            "scope_note": "Runs one read-only SELECT with the claim filter/order and LIMIT 1; "
                          "FOR UPDATE SKIP LOCKED and the stale-job UPDATE are excluded, "
                          "so this is not a full claim transaction timing.",
        }

    def finish_news_job(self, job_key: str, output_ref: str) -> None:
        from .postgres_access import DBWriterContext, open_observed_connection

        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="news.job_finish", writer_kind="news_job_finish",
            operation="finish_news_job", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE central_news_jobs SET state='COMPLETED',output_ref=%s,error='',updated_at=%s "
                "WHERE job_key=%s", (output_ref, time(), job_key),
            )
        from .diagnostic_metrics import record_writer_transaction
        record_writer_transaction("news_job_finish", 1,
                                  round((monotonic() - started_at) * 1000),
                                  db_call_id=writer.call_id)

    def retry_news_job(self, job_key: str, error: str, next_retry_at: float,
                        output_ref: str = "") -> None:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="news.job_retry", writer_kind="news_job_retry",
            operation="retry_news_job", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT attempts FROM central_news_jobs WHERE job_key=%s", (job_key,))
            row = cursor.fetchone()
            state = "FAILED" if row is not None and int(row[0]) >= 3 else "PENDING"
            cursor.execute(
                "UPDATE central_news_jobs SET state=%s,error=%s,next_retry_at=%s,output_ref=%s,"
                "updated_at=%s WHERE job_key=%s",
                (state, error[:1000], float(next_retry_at), output_ref, time(), job_key),
            )
        if state == "PENDING":
            _notify_news_job_wakeup(self)

    def claim_news_request(self, scope: str, *, scope_limit: int, hard_limit: int,
                           budget_date: str) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="news.request_budget", writer_kind=f"news_request:{scope}",
            operation="claim_news_request", rows_attempted=None,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("LOCK TABLE central_news_request_budget IN EXCLUSIVE MODE")
            cursor.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM central_news_request_budget WHERE budget_date=%s",
                (budget_date,),
            )
            total = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT request_count FROM central_news_request_budget WHERE budget_date=%s AND scope=%s",
                (budget_date, scope),
            )
            row = cursor.fetchone()
            if total >= int(hard_limit) or (row and int(row[0]) >= int(scope_limit)):
                return False
            cursor.execute(
                "INSERT INTO central_news_request_budget(budget_date,scope,request_count,updated_at) "
                "VALUES(%s,%s,1,%s) ON CONFLICT(budget_date,scope) DO UPDATE SET "
                "request_count=central_news_request_budget.request_count+1,updated_at=EXCLUDED.updated_at",
                (budget_date, scope, time()),
            )
            return True

    def news_request_count(self, budget_date: str) -> int:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.news_request_budget", writer_kind="request_count",
            operation="news_request_count", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM central_news_request_budget WHERE budget_date=%s",
                (budget_date,),
            )
            row = cursor.fetchone()
        return int(row[0]) if row else 0


def _enqueue_sqlite_news_ai_jobs(connection: sqlite3.Connection, values: list[dict[str, Any]]) -> int:
    count = 0
    for value in values:
        stock, identity = str(value["stock_code"]), str(value["identity"])
        row = connection.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=? AND identity=? ORDER BY accepted_sequence DESC LIMIT 1", (stock, identity),
        ).fetchone()
        if row is None:
            continue
        body = connection.execute(
            "SELECT body_revision_id,content_hash FROM central_news_body_revisions "
            "WHERE article_revision_id=? AND status IN ('fulltext','summary_only') "
            "ORDER BY accepted_sequence DESC LIMIT 1", (str(row[0]),),
        ).fetchone()
        if body is None:
            continue
        payload = dict(value["payload"])
        payload["body_revision_id"] = str(body[0])
        before = connection.total_changes
        _insert_sqlite_news_job(
            connection, str(row[0]), stock, str(value.get("target_id") or stock), "AI",
            str(body[1]), str(value["processing_version"]), payload, time(), str(body[0]),
        )
        count += int(connection.total_changes > before)
    return count


def _enqueue_postgres_news_ai_jobs(cursor: Any, values: list[dict[str, Any]]) -> int:
    count = 0
    for value in values:
        stock, identity = str(value["stock_code"]), str(value["identity"])
        cursor.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=%s AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1", (stock, identity),
        )
        row = cursor.fetchone()
        if row is None:
            continue
        cursor.execute(
            "SELECT body_revision_id,content_hash FROM central_news_body_revisions "
            "WHERE article_revision_id=%s AND status IN ('fulltext','summary_only') "
            "ORDER BY accepted_sequence DESC LIMIT 1", (str(row[0]),),
        )
        body = cursor.fetchone()
        if body is None:
            continue
        payload = dict(value["payload"])
        payload["body_revision_id"] = str(body[0])
        key = news_job_key("AI", str(value.get("target_id") or stock), str(body[0]), str(body[1]),
                           str(value["processing_version"]))
        cursor.execute(
            "INSERT INTO central_news_jobs VALUES(%s,%s,%s,%s,'AI',%s,%s,0,%s,'PENDING','','',%s,%s) "
            "ON CONFLICT(job_key) DO NOTHING RETURNING job_key",
            (key, str(row[0]), stock, str(value.get("target_id") or stock), str(body[1]),
             str(value["processing_version"]), time(),
             json.dumps(payload, ensure_ascii=False, separators=(",", ":")), time()),
        )
        count += int(cursor.fetchone() is not None)
    return count


def _news_job_rows(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    keys = ("job_key", "article_revision_id", "stock_code", "target_id", "stage",
            "input_hash", "processing_version", "attempts")
    return [{**dict(zip(keys, row[:8], strict=True)), "attempts": int(row[7]) + 1,
             "payload": row[8] if isinstance(row[8], dict) else json.loads(str(row[8])),
             "queued_at": float(row[9])} for row in rows]
