"""One-time NAS cleanup of legacy historical articles without finished BODY/RULE.

Run inside the server container, where KIWOOM_SERVER_DATABASE_URL is available.
The default mode only counts candidates. --execute writes a manifest for the PC
reimport ledger and deletes those candidates in bounded transactions.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg


LEGACY_SCOPES = ("historical_backfill", "historical_market_backfill")


def prepare(connection: psycopg.Connection) -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute("CREATE TEMP TABLE legacy_news ON COMMIT PRESERVE ROWS AS "
                       "SELECT accepted_sequence,article_revision_id,stock_code,identity,collection_scope "
                       "FROM central_news_article_revisions WHERE collection_scope=ANY(%s)",
                       (list(LEGACY_SCOPES),))
        cursor.execute("CREATE UNIQUE INDEX ON legacy_news(article_revision_id)")
        cursor.execute("CREATE INDEX ON legacy_news(accepted_sequence)")
        cursor.execute("CREATE TEMP TABLE finished_body ON COMMIT PRESERVE ROWS AS "
                       "SELECT DISTINCT j.article_revision_id FROM central_news_jobs j "
                       "JOIN legacy_news l USING(article_revision_id) "
                       "WHERE j.stage='BODY' AND j.state='COMPLETED'")
        cursor.execute("CREATE UNIQUE INDEX ON finished_body(article_revision_id)")
        cursor.execute("CREATE TEMP TABLE unfinished_rule ON COMMIT PRESERVE ROWS AS "
                       "SELECT DISTINCT j.article_revision_id FROM central_news_jobs j "
                       "JOIN legacy_news l USING(article_revision_id) "
                       "WHERE j.stage='RULE' AND j.state<>'COMPLETED'")
        cursor.execute("CREATE UNIQUE INDEX ON unfinished_rule(article_revision_id)")
        cursor.execute("CREATE TEMP TABLE purge_news ON COMMIT PRESERVE ROWS AS "
                       "SELECT l.* FROM legacy_news l "
                       "LEFT JOIN finished_body b USING(article_revision_id) "
                       "LEFT JOIN unfinished_rule r USING(article_revision_id) "
                       "WHERE b.article_revision_id IS NULL OR r.article_revision_id IS NOT NULL")
        cursor.execute("CREATE UNIQUE INDEX ON purge_news(article_revision_id)")
        cursor.execute("CREATE INDEX ON purge_news(accepted_sequence)")
        cursor.execute("SELECT COUNT(*) FROM purge_news")
        initially_unfinished = cursor.fetchone()[0]
        # Keep any revision already used as evidence or as a parent of a later
        # revision. Those links must not be left pointing to a deleted row.
        for table in ("central_news_ai_revisions", "central_news_event_revisions",
                      "central_news_event_membership_revisions"):
            cursor.execute(f"DELETE FROM purge_news p USING {table} x "
                           "WHERE x.article_revision_id=p.article_revision_id")
        cursor.execute("DELETE FROM purge_news p USING central_news_article_revisions x "
                       "WHERE x.revision_of=p.article_revision_id")
        cursor.execute("SELECT COUNT(*) FROM legacy_news")
        legacy = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM purge_news")
        purge = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM central_news_jobs j "
                       "JOIN legacy_news l USING(article_revision_id) "
                       "WHERE j.stage IN ('BODY','RULE') AND j.state<>'COMPLETED'")
        pending_jobs = cursor.fetchone()[0]
        cursor.execute("CREATE TEMP TABLE purge_observations ON COMMIT PRESERVE ROWS AS "
                       "SELECT o.observation_id,o.article_revision_id,o.run_id "
                       "FROM central_news_source_observations o "
                       "JOIN purge_news p USING(article_revision_id)")
        cursor.execute("CREATE UNIQUE INDEX ON purge_observations(observation_id)")
        cursor.execute("CREATE INDEX ON purge_observations(article_revision_id)")
        cursor.execute("CREATE TEMP TABLE affected_runs ON COMMIT PRESERVE ROWS AS "
                       "SELECT DISTINCT run_id FROM purge_observations")
    connection.commit()
    return {"legacy_articles": legacy, "unfinished_articles": initially_unfinished,
            "delete_articles": purge, "protected_references": initially_unfinished - purge,
            "cancel_jobs": pending_jobs}


def execute(connection: psycopg.Connection, manifest: Path, batch_size: int) -> dict[str, int]:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with connection.cursor() as cursor, manifest.open("w", encoding="utf-8") as output:
        cursor.execute("SELECT collection_scope,stock_code,identity FROM purge_news "
                       "ORDER BY accepted_sequence")
        for scope, stock, identity in cursor:
            output.write(json.dumps({"scope": scope, "stock_code": stock,
                                     "identity": identity}, ensure_ascii=False) + "\n")
    connection.commit()
    total_articles = total_jobs = 0
    after_sequence = 0
    while True:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE batch_legacy ON COMMIT DROP AS "
                           "SELECT * FROM legacy_news WHERE accepted_sequence>%s "
                           "ORDER BY accepted_sequence LIMIT %s", (after_sequence, batch_size))
            cursor.execute("SELECT MAX(accepted_sequence),COUNT(*) FROM batch_legacy")
            last_sequence, count = cursor.fetchone()
            if not count:
                connection.rollback()
                break
            after_sequence = last_sequence
            cursor.execute("CREATE INDEX ON batch_legacy(article_revision_id)")
            cursor.execute("CREATE TEMP TABLE batch_purge ON COMMIT DROP AS "
                           "SELECT p.* FROM purge_news p JOIN batch_legacy l USING(article_revision_id)")
            cursor.execute("CREATE INDEX ON batch_purge(article_revision_id)")
            cursor.execute("DELETE FROM central_news_jobs j USING batch_legacy l "
                           "WHERE j.article_revision_id=l.article_revision_id "
                           "AND j.stage IN ('BODY','RULE') AND j.state<>'COMPLETED'")
            total_jobs += cursor.rowcount
            cursor.execute("DELETE FROM central_news_source_observations x "
                           "USING purge_observations o,batch_purge p "
                           "WHERE x.observation_id=o.observation_id "
                           "AND o.article_revision_id=p.article_revision_id")
            for table in ("central_news_article_target_revisions",
                          "central_news_body_revisions", "central_news_jobs"):
                cursor.execute(f"DELETE FROM {table} x USING batch_purge p "
                               "WHERE x.article_revision_id=p.article_revision_id")
            cursor.execute("DELETE FROM central_documents d USING batch_purge p "
                           "WHERE d.collection='news_assessment' "
                           "AND d.document_key=p.article_revision_id")
            cursor.execute("DELETE FROM central_news_article_revisions a USING batch_purge p "
                           "WHERE a.article_revision_id=p.article_revision_id")
            total_articles += cursor.rowcount
            cursor.execute("DELETE FROM central_documents d USING batch_purge p "
                           "WHERE d.collection='news_article' AND d.owner=p.stock_code "
                           "AND d.document_key=p.identity AND NOT EXISTS ("
                           "SELECT 1 FROM central_news_article_revisions a "
                           "WHERE a.stock_code=d.owner AND a.identity=d.document_key)")
        connection.commit()
        print(json.dumps({"scanned_sequence": after_sequence, "deleted_articles": total_articles,
                          "cancelled_jobs": total_jobs}), flush=True)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM central_news_source_runs r USING affected_runs x "
                       "WHERE r.run_id=x.run_id "
                       "AND r.scope IN ('historical_backfill','historical_market_backfill',"
                       "'historical_market_pc_backfill','historical_news_pc_backfill') "
                       "AND NOT EXISTS (SELECT 1 FROM central_news_source_observations o "
                       "WHERE o.run_id=r.run_id)")
    connection.commit()
    return {"deleted_articles": total_articles, "cancelled_jobs": total_jobs,
            "manifest": str(manifest)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--manifest", type=Path,
                        default=Path("/app/data/maintenance/purged-legacy-news.jsonl"))
    args = parser.parse_args()
    if not 100 <= args.batch_size <= 10000:
        parser.error("batch-size must be 100..10000")
    database_url = os.environ.get("KIWOOM_SERVER_DATABASE_URL")
    if not database_url:
        raise SystemExit("KIWOOM_SERVER_DATABASE_URL is not configured")
    with psycopg.connect(database_url) as connection:
        counts = prepare(connection)
        print(json.dumps({"mode": "execute" if args.execute else "preview", **counts}), flush=True)
        if args.execute:
            result = execute(connection, args.manifest, args.batch_size)
            print(json.dumps({"mode": "done", **result}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
