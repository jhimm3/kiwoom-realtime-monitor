"""Identify one long PostgreSQL query without printing bind values or credentials."""

from __future__ import annotations

import json
import os
import re


def main() -> None:
    import psycopg

    with psycopg.connect(os.environ["KIWOOM_SERVER_DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pid,backend_type,state,COALESCE(wait_event_type,''),"
                "COALESCE(wait_event,''),"
                "round(EXTRACT(EPOCH FROM clock_timestamp()-query_start))::bigint,"
                "round(EXTRACT(EPOCH FROM clock_timestamp()-xact_start))::bigint,"
                "pg_blocking_pids(pid),query FROM pg_stat_activity "
                "WHERE pid=%s", (10523,),
            )
            row = cur.fetchone()
    if row is None:
        print(json.dumps({"pid": 10523, "present": False}))
        return
    pid, backend, state, wait_type, wait_event, age, xact_age, blocked, query = row
    statement = re.sub(r"'(?:''|[^'])*'", "'?'", query or "")
    statement = re.sub(r"\b\d+\b", "?", statement)
    statement = " ".join(statement.split())[:260]
    print(json.dumps({"pid": pid, "backend": backend, "state": state,
                      "wait": f"{wait_type}:{wait_event}", "query_age_seconds": age,
                      "transaction_age_seconds": xact_age, "blocked_by": blocked,
                      "redacted_query_prefix": statement}, ensure_ascii=False))


if __name__ == "__main__":
    main()
