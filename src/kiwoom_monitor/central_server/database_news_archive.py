"""Local news payload retention and its durable archive references."""

from __future__ import annotations

import json

from kiwoom_monitor.application.news_material import RECORDED_CONTRACT_FILINGS
from kiwoom_monitor.domain.news_observation import NewsArchiveUnavailable, stable_document_hash

from .database_codec import json_mapping


NEWS_ARCHIVE_REFERENCE = "news_body_archive_reference"
NEWS_BODY_COLUMNS = (
    "body_revision_id", "article_revision_id", "content_hash", "extractor_version",
    "fetched_at", "available_at", "status", "body_text", "error",
)


def _records(db, sql, parameters):
    cursor = db.execute(sql, parameters)
    keys = tuple(column[0] for column in cursor.description)
    return [dict(zip(keys, row, strict=True)) for row in cursor]


class SQLiteNewsArchiveStoreMixin:
    _news_archive_reader = None

    def set_news_archive_reader(self, reader) -> None:
        self._news_archive_reader = reader

    def read_news_archive_records(self, cutoff: float, *, after_sequence: int, limit: int) -> list[dict]:
        with self._connection() as db:
            db.execute("BEGIN")
            bodies = _records(db, f"""
                SELECT b.accepted_sequence,{','.join('b.' + column for column in NEWS_BODY_COLUMNS)}
                FROM central_news_body_revisions b
                JOIN central_news_article_revisions a USING(article_revision_id)
                WHERE b.accepted_sequence>? AND b.available_at<=? AND b.body_text<>''
                    AND a.collection_scope NOT LIKE 'historical_%'
                    AND NOT EXISTS (SELECT 1 FROM central_news_jobs j
                        WHERE j.state IN ('PENDING','RUNNING') AND j.article_revision_id=b.article_revision_id)
                    AND NOT EXISTS (SELECT 1 FROM central_news_article_target_revisions t
                        WHERE t.article_revision_id=b.article_revision_id AND t.relation_status='confirmed'
                            AND a.stock_code='GLOBAL'
                            AND NOT EXISTS (SELECT 1 FROM central_documents d
                                WHERE d.collection='news_assessment' AND d.owner=t.stock_code
                                    AND d.document_key=b.article_revision_id))
                ORDER BY b.accepted_sequence LIMIT ?
                """, (after_sequence, cutoff, limit))
            result = []
            for body in bodies:
                article_id, body_id = body["article_revision_id"], body["body_revision_id"]
                article = _records(db, "SELECT * FROM central_news_article_revisions WHERE article_revision_id=?",
                                   (article_id,))[0]
                filings = _records(db, """
                    SELECT owner,document_key,updated_at,document_json FROM central_documents
                    WHERE collection='news_official_filing'
                        AND json_extract(document_json,'$.body_revision_id')=?
                    ORDER BY owner,document_key
                    """, (body_id,))
                processed = {
                    "documents": _records(db, """
                        SELECT collection,owner,document_key,updated_at,document_json FROM central_documents
                        WHERE (collection='news_assessment' AND document_key=?
                                AND json_extract(document_json,'$.body_revision_id')=?)
                            OR (collection='news_original_publication' AND owner=?)
                            OR (collection=?
                                AND json_extract(document_json,'$.body_ref')=?)
                        ORDER BY collection,owner,document_key
                        """, (article_id, body_id, article_id, RECORDED_CONTRACT_FILINGS, body_id)),
                }
                for kind, table in (
                    ("event", "central_news_event_revisions"),
                    ("membership", "central_news_event_membership_revisions"),
                    ("ai", "central_news_ai_revisions"),
                ):
                    processed[kind] = _records(
                        db, f"SELECT * FROM {table} WHERE body_revision_id=? ORDER BY accepted_sequence", (body_id,))
                result.append({"body": body, "article": article, "filings": filings, "processed": processed})
        return result

    def retain_news_archive_reference(self, record: dict, reference: dict) -> None:
        body = record["body"]
        body_id = body["body_revision_id"]
        with self._lock, self._connection() as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            saved = db.execute(
                "SELECT body_text,content_hash FROM central_news_body_revisions WHERE body_revision_id=?",
                (body_id,),
            ).fetchone()
            existing = db.execute(
                "SELECT document_json FROM central_documents WHERE collection=? AND owner=? AND document_key=?",
                (NEWS_ARCHIVE_REFERENCE, body_id, body_id),
            ).fetchone()
            if existing and saved and not saved[0] and json_mapping(existing[0]) == reference:
                return
            if existing or saved != (body["body_text"], body["content_hash"]):
                raise ValueError("local news body differs from the accepted archive")
            for filing in record["filings"]:
                stripped = json_mapping(filing["document_json"])
                stripped.pop("filing_html", None)
                updated = db.execute("""
                    UPDATE central_documents SET document_json=?
                    WHERE collection='news_official_filing' AND owner=? AND document_key=? AND document_json=?
                    """, (json.dumps(stripped, ensure_ascii=False, separators=(",", ":")),
                          filing["owner"], filing["document_key"], filing["document_json"]))
                if updated.rowcount != 1:
                    raise ValueError("local official filing differs from the accepted archive")
            db.execute("""
                INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json)
                VALUES(?,?,?,?,?)
                """, (NEWS_ARCHIVE_REFERENCE, body_id, body_id, body["available_at"],
                      json.dumps(reference, ensure_ascii=False, separators=(",", ":"))))
            db.execute("UPDATE central_news_body_revisions SET body_text='' WHERE body_revision_id=?", (body_id,))

    def restore_archived_news_body(self, body: dict) -> dict:
        if body["body_text"] or body["status"] == "failed":
            return body
        if body["content_hash"] == stable_document_hash({"body": ""}):
            return body
        body_id = body["body_revision_id"]
        with self._connection() as db:
            row = db.execute(
                "SELECT document_json FROM central_documents WHERE collection=? AND owner=? AND document_key=?",
                (NEWS_ARCHIVE_REFERENCE, body_id, body_id),
            ).fetchone()
        if row is None or self._news_archive_reader is None:
            raise NewsArchiveUnavailable("news body archive reference is unavailable")
        try:
            archived = self._news_archive_reader(body_id, json_mapping(row[0]))["body"]
            restored = {column: archived[column] for column in NEWS_BODY_COLUMNS}
            if ({**restored, "body_text": ""} != body
                    or stable_document_hash({"body": restored["body_text"]}) != body["content_hash"]):
                raise ValueError("archived body differs from the local revision")
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
            raise NewsArchiveUnavailable("news body archive could not be read") from error
        return restored


def has_news_archive_reference(db, body_id: str) -> bool:
    return db.execute(
        "SELECT 1 FROM central_documents WHERE collection=? AND owner=? AND document_key=?",
        (NEWS_ARCHIVE_REFERENCE, body_id, body_id),
    ).fetchone() is not None
