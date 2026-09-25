"""Read-only PostgreSQL snapshot of long-running news queries and job-table stats."""

from __future__ import annotations

import hashlib
import json
import os


def classify(query: str) -> str:
    sql = query.lower()
    if "skip locked" in sql and "central_news_jobs" in sql:
        return "news_job_claim"
    if "central_news_jobs" in sql:
        return "news_jobs_other"
    if "central_documents" in sql:
        return "documents"
    if "central_news_article_revisions" in sql:
        return "news_articles"
    return "other"


def main() -> None:
    import psycopg

    with psycopg.connect(os.environ["KIWOOM_SERVER_DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pid,state,COALESCE(wait_event_type,''),COALESCE(wait_event,''),"
                "round(EXTRACT(EPOCH FROM clock_timestamp()-query_start))::bigint,"
                "pg_blocking_pids(pid),query FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid() AND state<>'idle' "
                "ORDER BY query_start LIMIT 8"
            )
            queries = [{"pid": pid, "state": state, "wait": f"{kind}:{event}",
                        "query_age_seconds": age, "blocked_by": blocked,
                        "query_kind": classify(sql),
                        "query_sha256": hashlib.sha256(sql.encode()).hexdigest()[:12]}
                       for pid, state, kind, event, age, blocked, sql in cur.fetchall()]
            cur.execute(
                "SELECT n_live_tup,n_dead_tup,seq_scan,idx_scan,last_analyze,last_autoanalyze "
                "FROM pg_stat_user_tables WHERE relname='central_news_jobs'"
            )
            row = cur.fetchone()
            table = dict(zip(("live_estimate", "dead_estimate", "seq_scans", "index_scans",
                              "last_analyze", "last_autoanalyze"), row, strict=True)) if row else {}
            for key in ("last_analyze", "last_autoanalyze"):
                if table.get(key) is not None:
                    table[key] = table[key].isoformat()
            cur.execute(
                "SELECT indexrelname,idx_scan,idx_tup_read,idx_tup_fetch "
                "FROM pg_stat_user_indexes WHERE relname='central_news_jobs' ORDER BY indexrelname"
            )
            indexes = [dict(zip(("name", "scans", "tuples_read", "tuples_fetched"), item,
                                strict=True)) for item in cur.fetchall()]
    print(json.dumps({"queries": queries, "news_jobs_table": table,
                      "news_jobs_indexes": indexes}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
