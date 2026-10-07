"""Cancel one verified stale, read-only historical news claim query."""

from __future__ import annotations

import json
import os


TARGET_PID = 10523
MIN_AGE_SECONDS = 600
PREFIX = (
    "SELECT j.job_key,j.article_revision_id,j.stock_code,j.target_id,j.stage,"
    "j.input_hash,j.processing_version,j.attempts,j.payload_json,j.updated_at "
    "FROM central_news_jobs j JOIN central_news_article_revisions a"
)


def main() -> None:
    import psycopg

    with psycopg.connect(os.environ["KIWOOM_SERVER_DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT backend_type,state,EXTRACT(EPOCH FROM clock_timestamp()-query_start),query "
                "FROM pg_stat_activity WHERE pid=%s", (TARGET_PID,)
            )
            row = cursor.fetchone()
            if row is None:
                print(json.dumps({"pid": TARGET_PID, "state": "gone", "cancelled": False}))
                return
            backend, state, age, query = row
            if (backend != "client backend" or state != "active"
                    or float(age or 0) < MIN_AGE_SECONDS
                    or not " ".join((query or "").split()).startswith(PREFIX)):
                print(json.dumps({"pid": TARGET_PID, "state": "not_matching",
                                  "cancelled": False, "age_seconds": round(float(age or 0))}))
                return
            cursor.execute("SELECT pg_cancel_backend(%s)", (TARGET_PID,))
            sent = bool(cursor.fetchone()[0])
    print(json.dumps({"pid": TARGET_PID, "state": "cancel_requested",
                      "cancelled": sent, "age_seconds": round(float(age or 0))}))


if __name__ == "__main__":
    main()
