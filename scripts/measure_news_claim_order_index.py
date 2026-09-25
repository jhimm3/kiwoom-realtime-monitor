"""Measure the historical search-news claim plan without claiming a job."""

from __future__ import annotations

import json
import os
from time import time
from typing import Any


QUERY = (
    "SELECT j.job_key,j.article_revision_id,j.stock_code,j.target_id,j.stage,"
    "j.input_hash,j.processing_version,j.attempts,j.payload_json,j.updated_at "
    "FROM central_news_jobs j JOIN central_news_article_revisions a "
    "ON a.article_revision_id=j.article_revision_id "
    "WHERE j.state='PENDING' AND j.stage=%s AND j.processing_version=%s "
    "AND j.next_retry_at<=%s "
    "AND a.collection_scope='historical_news_pc_backfill' "
    "ORDER BY j.updated_at LIMIT 1"
)


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    result = [plan]
    for child in plan.get("Plans", []):
        result.extend(_nodes(child))
    return result


def _summary(document: dict[str, Any]) -> dict[str, Any]:
    plan = document["Plan"]
    nodes = _nodes(plan)
    return {
        "planning_ms": document.get("Planning Time"),
        "execution_ms": document.get("Execution Time"),
        "nodes": [node["Node Type"] for node in nodes],
        "indexes": list(dict.fromkeys(
            node["Index Name"] for node in nodes if "Index Name" in node
        )),
        "sort_methods": [node.get("Sort Method") for node in nodes
                         if node["Node Type"] == "Sort"],
        "shared_hit_blocks": plan.get("Shared Hit Blocks"),
        "shared_read_blocks": plan.get("Shared Read Blocks"),
        "temp_read_blocks": plan.get("Temp Read Blocks"),
        "temp_written_blocks": plan.get("Temp Written Blocks"),
    }


def main() -> None:
    import psycopg

    with psycopg.connect(os.environ["KIWOOM_SERVER_DATABASE_URL"], autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout='15s'")
            params = ("BODY", "article-text-v7", time())
            cursor.execute(
                "EXPLAIN (FORMAT JSON) " + QUERY + " FOR UPDATE OF j SKIP LOCKED",
                params,
            )
            lock_plan = _summary(cursor.fetchone()[0][0])
            cursor.execute(
                "EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) " + QUERY,
                ("BODY", "article-text-v7", time()),
            )
            read_plan = _summary(cursor.fetchone()[0][0])
            cursor.execute(
                "SELECT i.indisvalid,pg_relation_size(i.indexrelid) FROM pg_index i "
                "WHERE i.indexrelid=to_regclass('public.idx_central_news_jobs_claim_order')"
            )
            index = cursor.fetchone()
    print(json.dumps({
        "index_valid": bool(index[0]) if index else False,
        "index_size_bytes": int(index[1]) if index else None,
        "claim_plan": lock_plan,
        "read_plan": read_plan,
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
