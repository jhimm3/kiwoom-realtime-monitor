"""Show active PostgreSQL query shapes without bind values or credentials."""

from __future__ import annotations

import json
import os
import re


def _redact(statement: str) -> str:
    statement = re.sub(r"'(?:''|[^'])*'", "'?'", statement)
    statement = re.sub(r"\b\d+\b", "?", statement)
    return " ".join(statement.split())[:320]


def main() -> None:
    import psycopg

    with psycopg.connect(os.environ["KIWOOM_SERVER_DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pid,backend_type,state,COALESCE(wait_event_type,''),"
                "COALESCE(wait_event,''),"
                "round(EXTRACT(EPOCH FROM clock_timestamp()-query_start))::bigint,"
                "pg_blocking_pids(pid),query FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                "AND state<>'idle' ORDER BY query_start LIMIT 8"
            )
            rows = cur.fetchall()
    print(json.dumps([
        {"pid": pid, "backend": backend, "state": state,
         "wait": f"{wait_type}:{wait_event}", "query_age_seconds": age,
         "blocked_by": blocked, "redacted_query_prefix": _redact(query or "")}
        for pid, backend, state, wait_type, wait_event, age, blocked, query in rows
    ], ensure_ascii=False))


if __name__ == "__main__":
    main()
