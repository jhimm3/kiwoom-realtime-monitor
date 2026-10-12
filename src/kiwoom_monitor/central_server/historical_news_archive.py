"""Read a sealed, PC-computed historical-news archive without side effects."""

from __future__ import annotations

import base64
import binascii
from contextlib import closing
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from typing import Any


_PROJECTION_SCHEMA = "historical-search-projection/v1"


class ArchiveUnavailableError(ValueError):
    """The pinned archive can no longer be read safely."""


class InvalidArchiveCursorError(ValueError):
    """The cursor is malformed or belongs to another dataset/filter."""


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class HistoricalNewsArchiveReader:
    """A pinned read-only dataset; each request gets its own SQLite connection."""

    def __init__(self, path: Path, *, cursor_key: bytes) -> None:
        if not isinstance(cursor_key, bytes) or not cursor_key:
            raise ValueError("archive cursor key is required")
        self._path = path.resolve(strict=True)
        self._cursor_key = cursor_key
        self._file_identity = self._identity()
        with closing(self._connect()) as db:
            try:
                report_row = db.execute(
                    "SELECT value_json FROM archive_build_manifest WHERE key='report'"
                ).fetchone()
                schema_version = json.loads(report_row[0]).get("archive_schema_version", 1) if report_row else 1
                projection_row = db.execute(
                    "SELECT value_json FROM archive_build_manifest "
                    "WHERE key=?", ("projection_v2" if schema_version == 2 else "prepared_search_projection",)
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                raise ValueError("historical archive manifest is unavailable") from exc
            if not report_row or not projection_row:
                raise ValueError("historical archive manifest is incomplete")
            report, projection = json.loads(report_row[0]), json.loads(projection_row[0])
            if report.get("profile_only"):
                raise ValueError("capacity samples cannot be served")
            dataset_id = report.get("dataset_id")
            if report.get("build_state") != "sealed" or not isinstance(dataset_id, str) or not dataset_id:
                raise ValueError("historical archive is not sealed")
            if schema_version not in (1, 2):
                raise ValueError("unsupported historical archive schema")
            if schema_version == 2 and (report.get('coverage_state') not in ('complete','complete_with_source_failures') or not report.get('seal_gate',{}).get('ready_for_seal')):
                raise ValueError('historical archive publication gate is incomplete')
            if projection.get("schema") != ("historical-news-projection/v2" if schema_version == 2 else _PROJECTION_SCHEMA) or projection.get("state") != "complete" \
               or (schema_version == 1 and projection.get("projected") != projection.get("sources")):
                raise ValueError("historical search projection is incomplete")
            self.dataset_id = dataset_id
            self.schema_version = schema_version

    def _identity(self) -> tuple[int, int, int, int]:
        info = self._path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    def _connect(self) -> sqlite3.Connection:
        if self._identity() != self._file_identity:
            raise ArchiveUnavailableError("historical archive file changed after reader initialization")
        db = sqlite3.connect(self._path.as_uri() + "?mode=ro", uri=True, timeout=5)
        db.execute("PRAGMA query_only=ON")
        return db

    def _filter_hash(self, stock_code: str) -> str:
        value = json.dumps({"scope": "historical_search", "stock_code": stock_code},
                           sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _cursor(self, stock_code: str, last: tuple[int, int, str]) -> str:
        value = {"v": 1, "dataset_id": self.dataset_id,
                 "filter_hash": self._filter_hash(stock_code), "last": last}
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.digest(self._cursor_key, payload, "sha256")
        return _encode(payload) + "." + _encode(signature)

    def _parse_cursor(self, stock_code: str, cursor: str) -> tuple[int, int, str]:
        try:
            if len(cursor) > 2048:
                raise ValueError("archive cursor is too long")
            payload_part, signature_part = cursor.split(".")
            payload, signature = _decode(payload_part), _decode(signature_part)
            if not hmac.compare_digest(hmac.digest(self._cursor_key, payload, "sha256"), signature):
                raise ValueError("archive cursor signature is invalid")
            value = json.loads(payload)
            last = value["last"]
            if not isinstance(value, dict) or value.get("v") != 1 or \
               value.get("dataset_id") != self.dataset_id or \
               value.get("filter_hash") != self._filter_hash(stock_code) or \
               not isinstance(last, list) or len(last) != 3 or \
               type(last[0]) is not int or type(last[1]) is not int or \
               not isinstance(last[2], str):
                raise ValueError("archive cursor does not match the dataset or filter")
            return last[0], last[1], last[2]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
            raise InvalidArchiveCursorError("invalid historical archive cursor") from exc

    def search_page(self, stock_code: str, *, limit: int = 100,
                    cursor: str | None = None) -> dict[str, Any]:
        if not stock_code or not isinstance(stock_code, str):
            raise ValueError("stock_code is required")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        last = self._parse_cursor(stock_code, cursor) if cursor is not None else None
        continuation = (
            "AND (sort_missing>? OR (sort_missing=? AND "
            "(sort_us<? OR (sort_us=? AND identity>?)))) " if last else ""
        )
        parameters: tuple[Any, ...] = ((stock_code, last[0], last[0], last[1],
                                        last[1], last[2], limit + 1) if last
                                       else (stock_code, limit + 1))
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT identity,article_revision_id,body_revision_id,event_id,"
                "event_revision_id,membership_revision_id,source_kind,body_status,"
                "assessment_status,sort_missing,sort_us,display_json "
                "FROM archive_search_projection WHERE stock_code=? " + continuation +
                "ORDER BY sort_missing,sort_us DESC,identity LIMIT ?", parameters,
            ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [{"identity": row[0], "article_revision_id": row[1],
                  "body_revision_id": row[2], "event_id": row[3],
                  "event_revision_id": row[4], "membership_revision_id": row[5],
                  "source_kind": row[6], "body_status": row[7],
                  "assessment_status": row[8], "display": json.loads(row[11])}
                 for row in rows]
        next_cursor = (self._cursor(stock_code, (rows[-1][9], rows[-1][10], rows[-1][0]))
                       if has_more else None)
        return {"dataset_id": self.dataset_id, "items": items, "next_cursor": next_cursor}

    def article_by_id(self, article_revision_id: str, *,
                      body_revision_id: str | None = None) -> dict[str, Any] | None:
        """Return only stored, exact-ID history; never infer or compute missing fields."""
        if self.schema_version == 2:
            return self._article_v2(article_revision_id, body_revision_id=body_revision_id)
        if not article_revision_id:
            raise ValueError("article_revision_id is required")
        with closing(self._connect()) as db:
            article = db.execute(
                "SELECT stock_code,identity,published_at,received_at,available_at,"
                "revision_of,document_json FROM central_news_article_revisions "
                "WHERE article_revision_id=?", (article_revision_id,),
            ).fetchone()
            if article is None:
                return None
            if body_revision_id is not None:
                body = db.execute(
                    "SELECT body_revision_id,status,body_text,error,fetched_at,available_at "
                    "FROM central_news_body_revisions WHERE body_revision_id=? "
                    "AND article_revision_id=?", (body_revision_id, article_revision_id),
                ).fetchone()
                if body is None:
                    raise ValueError("body_revision_id does not belong to article_revision_id")
            else:
                body = db.execute(
                    "SELECT body_revision_id,status,body_text,error,fetched_at,available_at "
                    "FROM central_news_body_revisions WHERE article_revision_id=? "
                    "ORDER BY accepted_sequence DESC LIMIT 1", (article_revision_id,),
                ).fetchone()
            body_id = body[0] if body else None
            memberships = db.execute(
                "SELECT m.membership_revision_id,m.event_id,m.event_revision_id,"
                "m.relation,m.available_at,e.revision_of,e.result_json "
                "FROM central_news_event_membership_revisions m "
                "JOIN central_news_event_revisions e "
                "ON e.event_revision_id=m.event_revision_id AND e.event_id=m.event_id "
                "WHERE m.article_revision_id=? AND m.body_revision_id=? "
                "ORDER BY m.accepted_sequence", (article_revision_id, body_id),
            ).fetchall() if body_id else []
            projection = db.execute(
                "SELECT display_json FROM archive_search_projection "
                "WHERE stock_code=? AND identity=? AND article_revision_id=? "
                "AND body_revision_id IS ?",
                (article[0], article[1], article_revision_id, body_id),
            ).fetchone()
            display = json.loads(projection[0]) if projection else None
            assessment = display.get("assessment") if display else None
            assessment_status = display.get("assessment_status") if display else "unassessed"
            return {
                "dataset_id": self.dataset_id, "article_revision_id": article_revision_id,
                "stock_code": article[0], "identity": article[1], "published_at": article[2],
                "received_at": article[3], "available_at": article[4],
                "revision_of": article[5], "document": json.loads(article[6]),
                "body": ({"body_revision_id": body[0], "status": body[1],
                          "text": body[2], "error": body[3], "fetched_at": body[4],
                          "available_at": body[5]} if body else None),
                "assessment": assessment, "assessment_status": assessment_status,
                "events": [{"membership_revision_id": row[0], "event_id": row[1],
                            "event_revision_id": row[2], "relation": row[3],
                            "membership_available_at": row[4], "revision_of": row[5],
                            "result": json.loads(row[6])} for row in memberships],
            }

    def page_v2(self, kind: str, *, stock_code: str | None = None,
                source: str | None = None, limit: int = 100,
                cursor: str | None = None) -> dict[str, Any]:
        if self.schema_version != 2:
            raise ValueError("v2 archive required")
        if kind not in ("search", "market") or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid archive page arguments")
        if kind == "search":
            import re
            if not isinstance(stock_code, str) or not re.fullmatch(r"[0-9A-Z]{6}", stock_code) or source is not None:
                raise ValueError("search requires a stock code")
            owner = stock_code
        else:
            if stock_code is not None or source not in (None, "flash", "world"):
                raise ValueError("invalid market filter")
            owner = "GLOBAL"
        filter_hash = hashlib.sha256(json.dumps({"kind":kind,"owner":owner,"source":source},sort_keys=True,separators=(",",":")).encode()).hexdigest()
        last = None
        if cursor is not None:
            try:
                if len(cursor) > 2048:
                    raise ValueError("oversized cursor")
                encoded, signature = cursor.split(".")
                payload = _decode(encoded)
                if not hmac.compare_digest(hmac.digest(self._cursor_key,payload,"sha256"),_decode(signature)):
                    raise ValueError("cursor signature")
                value = json.loads(payload)
                last = value["last"]
                if value.get("v") != 2 or value.get("dataset_id") != self.dataset_id or value.get("filter_hash") != filter_hash or not isinstance(last,list) or len(last)!=3 or type(last[0]) is not int or last[0] not in (0,1) or type(last[1]) is not int or not isinstance(last[2],str):
                    raise ValueError("cursor context")
            except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
                raise InvalidArchiveCursorError("invalid v2 archive cursor") from exc
        query = "SELECT p.identity,p.article_revision_id,p.body_revision_id,p.sort_missing,p.sort_us,p.display_json FROM archive_projection_v2 p WHERE p.kind=? AND p.owner=? "
        parameters: list[Any] = [kind,owner]
        if source:
            query += "AND EXISTS(SELECT 1 FROM archive_market_sources_v2 s WHERE s.identity=p.identity AND s.source=?) "
            parameters.append(source)
        if last:
            query += "AND (p.sort_missing>? OR (p.sort_missing=? AND (p.sort_us<? OR (p.sort_us=? AND p.identity>?)))) "
            parameters.extend((last[0],last[0],last[1],last[1],last[2]))
        query += "ORDER BY p.sort_missing,p.sort_us DESC,p.identity LIMIT ?"
        parameters.append(limit+1)
        with closing(self._connect()) as db:
            rows = db.execute(query,parameters).fetchall()
        more = len(rows)>limit
        rows = rows[:limit]
        items = [{"identity":r[0],"article_revision_id":r[1],"body_revision_id":r[2],"display":json.loads(r[5])} for r in rows]
        continuation = None
        if more:
            payload = json.dumps({"v":2,"dataset_id":self.dataset_id,"filter_hash":filter_hash,"last":[rows[-1][3],rows[-1][4],rows[-1][0]]},sort_keys=True,separators=(",",":")).encode()
            continuation = _encode(payload)+"."+_encode(hmac.digest(self._cursor_key,payload,"sha256"))
        return {"dataset_id":self.dataset_id,"kind":kind,"items":items,"next_cursor":continuation}

    def _article_v2(self, article_id: str, *, body_revision_id: str | None) -> dict[str, Any] | None:
        if not article_id:
            raise ValueError("article_revision_id required")
        with closing(self._connect()) as db:
            article = db.execute("SELECT stock_code,identity,published_at,received_at,available_at,revision_of,document_json FROM central_news_article_revisions WHERE article_revision_id=?",(article_id,)).fetchone()
            if article is None:
                return None
            query = "SELECT body_revision_id,status,body_text,error,fetched_at,available_at FROM central_news_body_revisions WHERE article_revision_id=? "
            parameters = [article_id]
            if body_revision_id is not None:
                query += "AND body_revision_id=? "
                parameters.append(body_revision_id)
            body = db.execute(query+"ORDER BY accepted_sequence DESC LIMIT 1",parameters).fetchone()
            if body_revision_id is not None and body is None:
                raise ValueError("BODY ID does not belong to article")
            body_id = body[0] if body else None
            targets = db.execute("SELECT target_id,target_name,computed_json,verification_state,fingerprint,computed_at,event_state,event_id,event_revision_id,membership_revision_id FROM archive_targets_v2 WHERE article_revision_id=? AND body_revision_id=? ORDER BY target_id,scope,identity",(article_id,body_id)).fetchall() if body_id else []
            memberships = db.execute("SELECT m.membership_revision_id,m.event_id,m.event_revision_id,m.relation,m.available_at,e.stock_code,e.revision_of,e.result_json FROM central_news_event_membership_revisions m JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id AND e.event_id=m.event_id WHERE m.article_revision_id=? AND m.body_revision_id=? ORDER BY m.accepted_sequence",(article_id,body_id)).fetchall() if body_id else []
            clean = db.execute("SELECT text FROM archive_clean_bodies_v2 WHERE body_revision_id=?",(body_id,)).fetchone() if body_id else None
            contexts = db.execute('SELECT scope,stock_code,identity,context_json FROM archive_input_context_v2 WHERE article_revision_id=? AND body_revision_id=? ORDER BY scope,stock_code,identity',(article_id,body_id)).fetchall() if body_id else []
            return {"dataset_id":self.dataset_id,"article_revision_id":article_id,"stock_code":article[0],"identity":article[1],"published_at":article[2],"received_at":article[3],"available_at":article[4],"revision_of":article[5],"document":json.loads(article[6]),
                    "body":({"body_revision_id":body[0],"status":body[1],"text":body[2],"display_text":clean[0] if clean else "","error":body[3],"fetched_at":body[4],"available_at":body[5]} if body else None),
                    "target_assessments":[{"target_id":r[0],"target_name":r[1],"result":json.loads(r[2]),"verification_state":r[3],"fingerprint":r[4],"computed_at":r[5],"event_state":r[6],"event_id":r[7],"event_revision_id":r[8],"membership_revision_id":r[9]} for r in targets],
                    "assessment_status":"verified" if targets else "unassessed",
                    "source_contexts":[{"scope":r[0],"owner":r[1],"identity":r[2],"metadata":json.loads(r[3])} for r in contexts],
                    "events":[{"membership_revision_id":r[0],"event_id":r[1],"event_revision_id":r[2],"relation":r[3],"available_at":r[4],"target_id":r[5],"revision_of":r[6],"result":json.loads(r[7])} for r in memberships]}
