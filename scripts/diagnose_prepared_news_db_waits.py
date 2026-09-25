"""Read-only five-second PostgreSQL wait sample during prepared-news import."""

from __future__ import annotations

import collections
import json
import os
import time


def _category(statement: str) -> str:
    text = statement.lower()
    for name in ("central_news_jobs", "central_news_article_revisions",
                 "central_news_body_revisions", "central_news_event_revisions",
                 "central_documents", "central_news_source_runs"):
        if name in text:
            return name.removeprefix("central_")
    return "other"


def main() -> None:
    import psycopg

    database_url = os.environ["KIWOOM_SERVER_DATABASE_URL"]
    counts: collections.Counter[str] = collections.Counter()
    max_query_ms: dict[str, int] = {}
    blockers: collections.Counter[str] = collections.Counter()
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT xact_commit,blks_read,blks_hit,temp_bytes,deadlocks "
                           "FROM pg_stat_database WHERE datname=current_database()")
            before = cursor.fetchone()
            for _ in range(20):
                cursor.execute(
                    "SELECT pid,state,COALESCE(wait_event_type,''),COALESCE(wait_event,''),"
                    "EXTRACT(EPOCH FROM clock_timestamp()-query_start)*1000,"
                    "pg_blocking_pids(pid),query FROM pg_stat_activity "
                    "WHERE datname=current_database() AND pid<>pg_backend_pid()"
                )
                for _, state, wait_type, wait_event, age, blocked_by, statement in cursor.fetchall():
                    if state == "idle":
                        counts["idle"] += 1
                        continue
                    category = _category(statement or "")
                    key = f"{category}|{state}|{wait_type or 'CPU'}:{wait_event or '-'}"
                    counts[key] += 1
                    max_query_ms[key] = max(max_query_ms.get(key, 0), int(age or 0))
                    if blocked_by:
                        blockers[category] += 1
                time.sleep(0.25)
            cursor.execute("SELECT xact_commit,blks_read,blks_hit,temp_bytes,deadlocks "
                           "FROM pg_stat_database WHERE datname=current_database()")
            after = cursor.fetchone()
    delta = dict(zip(("transactions", "blocks_read", "blocks_hit", "temp_bytes", "deadlocks"),
                     (int(end) - int(start) for start, end in zip(before, after, strict=True)),
                     strict=True))
    print(json.dumps({"seconds": 5, "active_samples": dict(counts),
                      "max_query_ms": max_query_ms, "blocking_samples": dict(blockers),
                      "database_delta": delta}, ensure_ascii=False))


if __name__ == "__main__":
    main()
