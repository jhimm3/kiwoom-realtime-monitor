"""Recover confirmed legacy KIND TOC failures into a new local SQLite copy."""

from __future__ import annotations

import argparse
from contextlib import closing
from functools import partial
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from kiwoom_monitor.application.news_material import official_filing_ref
from kiwoom_monitor.central_server.database_news_revisions import _accept_news_job_result
from kiwoom_monitor.central_server.news_jobs import official_filing_body_result
from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION
from kiwoom_monitor.domain.time_basis import kst_now
from kiwoom_monitor.infrastructure.official_news_filing import (
    KIND_DOCUMENT_UNVERIFIED, _filing_body, _kind_document, _read,
)


_FAILURE = (
    "j.stage='BODY' AND j.state='FAILED' AND j.error=? AND j.processing_version=? "
    "AND EXISTS (SELECT 1 FROM central_news_body_revisions f WHERE f.body_revision_id=j.output_ref "
    "AND f.article_revision_id=j.article_revision_id AND f.status='failed' AND f.error=j.error)"
)
_FAILURE_ARGS = (KIND_DOCUMENT_UNVERIFIED, ARTICLE_BODY_EXTRACTOR_VERSION)
_FULLTEXT_EXISTS = (
    "EXISTS (SELECT 1 FROM central_news_body_revisions b "
    "WHERE b.article_revision_id=j.article_revision_id AND b.status='fulltext')"
)
_ARCHIVE = "collection='news_official_filing' AND json_extract(document_json,'$.filing_html')<>''"


def _save(db, job_key, attempts, filing, payload, now_provider) -> bool:
    result = official_filing_body_result(filing, payload, now_provider().timestamp())
    filing_ref = official_filing_ref(filing["document_url"])
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM central_documents WHERE " + _ARCHIVE +
            " AND json_extract(document_json,'$.filing_ref')=? LIMIT 1", (filing_ref,),
        ).fetchone():
            return False
        changed = db.execute(
            "UPDATE central_news_jobs AS j SET state='RUNNING',attempts=attempts+1,updated_at=?,"
            "body_processor_owner='',body_input_ref=NULL WHERE job_key=? AND attempts=? AND " +
            _FAILURE + " AND NOT " + _FULLTEXT_EXISTS,
            (now_provider().timestamp(), job_key, attempts, *_FAILURE_ARGS),
        ).rowcount
        if not changed:
            return False
        receipt, _ = _accept_news_job_result(
            db, job_key, result, attempts=attempts + 1, postgres=False, now_provider=now_provider,
        )
        if receipt["state"] != "completed":
            raise RuntimeError("KIND recovery did not commit an official BODY")
    return True


def recover(db: sqlite3.Connection, *, read=None, now_provider=kst_now) -> list[dict[str, str]]:
    read = read or partial(_read, timeout=8.0)
    jobs = db.execute(
        "SELECT j.job_key,j.attempts,j.payload_json," + _FULLTEXT_EXISTS +
        " FROM central_news_jobs j WHERE " + _FAILURE +
        " ORDER BY j.stock_code='GLOBAL',j.updated_at DESC,j.job_key", _FAILURE_ARGS,
    ).fetchall()
    if not jobs:
        return []
    archives = (json.loads(value) for value, in db.execute(
        "SELECT document_json FROM central_documents WHERE " + _ARCHIVE,
    ))
    saved = {ref for row in archives for url in (row["filing_url"], row["document_url"])
             if (ref := official_filing_ref(url))}
    report = []
    for job_key, attempts, raw, has_fulltext in jobs:
        payload = json.loads(raw)
        url = next((link for key in ("original_link", "link")
                    if official_filing_ref(link := payload.get(key))), "")
        ref = official_filing_ref(url)
        if not ref.startswith("kind:") or urlsplit(url).path.startswith("/external/"):
            continue
        if has_fulltext or ref in saved:
            status = "already_saved"
        else:
            try:
                body, parent, has_toc = _kind_document(url, ref.split(":", 1)[1], read)
                body_ref = official_filing_ref(body)
                if not has_toc:
                    status = "toc_not_confirmed"
                elif body_ref in saved:
                    status = "already_saved"
                else:
                    filing = _filing_body(url, body, parent, read)
                    status = "recovered" if _save(db, job_key, attempts, filing, payload, now_provider) else "no_longer_needed"
                    if status == "recovered":
                        saved.update((ref, body_ref))
            except (OSError, ValueError) as error:
                report.append({"filing": ref, "status": "unresolved", "reason": str(error)})
                continue
        report.append({"filing": ref, "status": status})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="SQLite source opened read-only")
    parser.add_argument("--output", type=Path, required=True, help="new local SQLite copy receiving recovered bodies")
    args = parser.parse_args()
    with closing(sqlite3.connect(args.source.resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as source:
        # Exclusive creation rejects existing files, including the input DB.
        with args.output.open("xb"):
            pass
        with closing(sqlite3.connect(args.output)) as output:
            source.backup(output)
            output.execute("PRAGMA foreign_keys=ON")
            report = recover(output)
    print(json.dumps(report, ensure_ascii=False))
    return int(any(item["status"] == "unresolved" for item in report))


if __name__ == "__main__":
    raise SystemExit(main())
