"""Add the historical-news claim ordering index without changing schema version.

Run this inside the NAS server container, where KIWOOM_SERVER_DATABASE_URL is set.
The concurrent build permits normal writes while PostgreSQL creates the index.
"""

from __future__ import annotations

import json
import os
from time import monotonic


INDEX_NAME = "idx_central_news_jobs_claim_order"
INDEX_REGCLASS = f"public.{INDEX_NAME}"
LOCK_NAME = "kiwoom:central_news_jobs:claim_order_index"
CREATE_SQL = (
    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
    "ON public.central_news_jobs(stage,processing_version,updated_at,next_retry_at) "
    "WHERE state='PENDING'"
)


def _index_state(cursor: object) -> dict[str, object] | None:
    cursor.execute(
        "SELECT i.indisvalid,i.indisready,pg_get_indexdef(i.indexrelid),"
        "pg_relation_size(i.indexrelid) FROM pg_index i "
        "WHERE i.indexrelid=to_regclass(%s)",
        (INDEX_REGCLASS,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    valid, ready, definition, size_bytes = row
    return {
        "valid": bool(valid),
        "ready": bool(ready),
        "definition": str(definition),
        "size_bytes": int(size_bytes),
    }


def _matches_expected_index(state: dict[str, object]) -> bool:
    definition = " ".join(str(state["definition"]).lower().replace('"', "").split())
    return (
        "central_news_jobs" in definition
        and "(stage, processing_version, updated_at, next_retry_at)" in definition
        and "state" in definition
        and "pending" in definition
    )


def main() -> None:
    import psycopg

    database_url = os.environ["KIWOOM_SERVER_DATABASE_URL"]
    started = monotonic()
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),to_regclass('public.central_news_jobs')")
            database_name, table = cursor.fetchone()
            if table is None:
                raise RuntimeError("public.central_news_jobs 테이블이 없습니다.")
            cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (LOCK_NAME,))
            if not cursor.fetchone()[0]:
                raise RuntimeError("같은 인덱스 생성 작업이 이미 실행 중입니다.")
            try:
                existing = _index_state(cursor)
                if existing is not None:
                    if not existing["valid"] or not existing["ready"]:
                        raise RuntimeError("동일 이름의 미완성 인덱스가 있습니다. 상태를 확인한 뒤 정리해야 합니다.")
                    if not _matches_expected_index(existing):
                        raise RuntimeError("동일 이름의 다른 인덱스가 있습니다. 자동으로 변경하지 않습니다.")
                    outcome = "already_ready"
                else:
                    print(json.dumps({
                        "state": "building", "database": database_name, "index": INDEX_NAME,
                    }, ensure_ascii=False), flush=True)
                    cursor.execute(CREATE_SQL)
                    outcome = "created"
                state = _index_state(cursor)
                if state is None or not state["valid"] or not state["ready"]:
                    raise RuntimeError("생성 후 인덱스 유효성을 확인하지 못했습니다.")
                if not _matches_expected_index(state):
                    raise RuntimeError("생성 후 인덱스 정의가 예상과 다릅니다.")
                print(json.dumps({
                    "state": outcome,
                    "database": database_name,
                    "index": INDEX_NAME,
                    "valid": state["valid"],
                    "size_bytes": state["size_bytes"],
                    "elapsed_seconds": round(monotonic() - started, 3),
                }, ensure_ascii=False))
            finally:
                cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))


if __name__ == "__main__":
    main()
