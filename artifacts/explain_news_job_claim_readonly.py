"""Read-only, plan-only PostgreSQL EXPLAIN for the news worker claim path."""

from __future__ import annotations

import json
import os
import time

import psycopg


CLAIM_SELECT = (
    "SELECT job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
    "processing_version,attempts,payload_json,updated_at FROM central_news_jobs "
    "WHERE state='PENDING' AND next_retry_at<=%s "
    "AND (stage NOT IN ('BODY','RULE') OR NOT EXISTS ("
    "SELECT 1 FROM central_news_article_revisions a "
    "WHERE a.article_revision_id=central_news_jobs.article_revision_id "
    "AND a.collection_scope IN ('historical_backfill','historical_market_backfill',"
    "'historical_market_pc_backfill','historical_news_pc_backfill'))) "
    "ORDER BY CASE WHEN %s<>'' AND stage=%s THEN -1 "
    "WHEN %s<>'' AND (stock_code=%s OR target_id=%s) AND stage='BODY' THEN 0 "
    "WHEN %s<>'' AND (stock_code=%s OR target_id=%s) THEN 1 "
    "WHEN stage='BODY' AND stock_code<>'GLOBAL' THEN 2 "
    "WHEN stage='BODY' THEN 3 WHEN stage='AI' THEN 4 ELSE 5 END,"
    "CASE WHEN stage='BODY' THEN -updated_at ELSE updated_at END "
    "FOR UPDATE SKIP LOCKED LIMIT %s"
)


def _nodes(node: dict) -> list[dict[str, object]]:
    fields = ("Node Type", "Relation Name", "Index Name", "Plan Rows", "Plan Width")
    result = [{key: node[key] for key in fields if key in node}]
    for child in node.get("Plans", []):
        result.extend(_nodes(child))
    return result


def main() -> None:
    now_epoch = time.time()
    plans: list[dict[str, object]] = []
    with psycopg.connect(os.environ["KIWOOM_SERVER_DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SET default_transaction_read_only=on")
            cursor.execute("SELECT current_database(),current_setting('server_version')")
            database, version = cursor.fetchone()
            cursor.execute(
                "EXPLAIN (FORMAT JSON) UPDATE central_news_jobs "
                "SET state='PENDING',updated_at=%s "
                "WHERE state='RUNNING' AND updated_at<%s",
                (now_epoch, now_epoch - 120.0),
            )
            value = cursor.fetchone()[0]
            plan = value[0]["Plan"] if isinstance(value, list) else json.loads(value)[0]["Plan"]
            plans.append({"operation": "recover_stale_running", "nodes": _nodes(plan)})
            for stage in ("BODY", "RULE"):
                params = (
                    now_epoch, stage, stage,
                    "", "", "", "", "", "", 1,
                )
                cursor.execute("EXPLAIN (FORMAT JSON) " + CLAIM_SELECT, params)
                value = cursor.fetchone()[0]
                plan = value[0]["Plan"] if isinstance(value, list) else json.loads(value)[0]["Plan"]
                plans.append({"operation": "claim_candidate_select", "preferred_stage": stage,
                              "nodes": _nodes(plan)})
    print(json.dumps({"mode": "read-only EXPLAIN without ANALYZE; no statement executed",
                      "database": database, "postgresql": version, "plans": plans},
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
