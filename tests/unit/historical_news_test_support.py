from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from kiwoom_monitor.central_server.database import SQLiteQueryStore, stable_document_hash
from scripts.build_prepared_historical_search_projection import build as build_search_projection
from scripts.finalize_prepared_historical_article_bodies import finalize as finalize_bodies
from scripts.finalize_prepared_historical_assessments import finalize as finalize_assessments
from scripts.finalize_prepared_historical_news_events import finalize as finalize_events
from scripts.preprocess_historical_news_to_nas import prepare_job
from scripts.stage_prepared_historical_news_archive import stage
from scripts.verify_prepared_historical_news_rules import verify


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepared_article_body_fixture(root: Path) -> tuple[Path, Path, Path, str]:
    prepared, archive, raw = (root / name for name in
                              ("prepared.sqlite3", "archive.sqlite3", "raw.sqlite3"))
    with closing(sqlite3.connect(prepared)) as db:
        db.execute("CREATE TABLE prepared_news (scope TEXT,stock_code TEXT,"
                   "identity TEXT,state TEXT,article_json TEXT,body_json TEXT,"
                   "rules_json TEXT,error TEXT,updated_at REAL,"
                   "PRIMARY KEY(scope,stock_code,identity))")
        for identity, body_status, body_text in [
            ("a", "fulltext", "verified text"),
            ("b", "summary_only", "summary text"),
            ("c", "fulltext", "unverified text"),
            ("d", "fulltext", "hash only text"),
        ]:
            document = {"stock_code": "005930", "identity": identity,
                        "stock_name": "Samsung", "title": identity,
                        "published_at": "2026-09-25T09:00:00+09:00",
                        "historical_source": {"provider": "naver", "office_id": "001",
                                              "article_id": identity}}
            article = {"stock_code": "005930", "identity": identity,
                       "document": document, "targets": [["005930", "Samsung"]]}
            body = {"body_status": body_status, "body_text": body_text,
                    "fetched_at": 1790300000.0, "source_url": "https://example.test/" + identity}
            rules = [{"target_id": "005930", "stock_name": "Samsung",
                      "assessment": {"sentiment": "neutral"}, "core_sentences": [],
                      "rule_result": {"rule_version": "supply-contract-rule-v2"}
                      if identity == "b" else None}]
            db.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)", (
                "historical_backfill", "005930", identity, "ready",
                json.dumps(article), json.dumps(body), json.dumps(rules), "", 1.0,
            ))
        db.commit()
    SQLiteQueryStore(archive).initialize()
    with closing(sqlite3.connect(archive)) as db:
        db.execute("CREATE TABLE archive_build_manifest (key TEXT PRIMARY KEY,value_json TEXT)")
        db.execute("INSERT INTO archive_build_manifest VALUES('report',?)",
                   (json.dumps({"build_state": "building"}),))
        with closing(sqlite3.connect(prepared)) as source:
            article = json.loads(source.execute(
                "SELECT article_json FROM prepared_news WHERE identity='a'"
            ).fetchone()[0])
        document = article["document"]
        db.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
            "received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("seed-article", "005930", "a", stable_document_hash(document),
             "naver_historical_web", document["published_at"], 1.0, 1.0,
             "historical_news_pc_backfill", None, json.dumps(document)),
        )
        db.execute(
            "INSERT INTO central_news_body_revisions("
            "body_revision_id,article_revision_id,content_hash,extractor_version,"
            "fetched_at,available_at,status,body_text,error) VALUES(?,?,?,?,?,?,?,?,?)",
            ("seed-body", "seed-article", stable_document_hash({"body": "verified text"}),
             "article-text-v7", 1.0, 1.0, "fulltext", "verified text", ""),
        )
        db.commit()
    with closing(sqlite3.connect(raw)) as db:
        db.execute("CREATE TABLE news_article_body_snapshots ("
                   "provider TEXT,office_id TEXT,article_id TEXT,"
                   "extractor_version TEXT,body_sha256 TEXT,fetched_at TEXT)")
        db.execute("INSERT INTO news_article_body_snapshots VALUES(?,?,?,?,?,?)",
                   ("naver", "001", "a", "article-text-v7",
                    hashlib.sha256(b"verified text").hexdigest(),
                    datetime.fromtimestamp(1790300000, timezone.utc).isoformat()))
        db.execute("INSERT INTO news_article_body_snapshots VALUES(?,?,?,?,?,?)",
                   ("naver", "001", "d", "article-text-v7",
                    hashlib.sha256(b"hash only text").hexdigest(),
                    datetime.fromtimestamp(1790300001, timezone.utc).isoformat()))
        db.commit()
    digest = sha256_file(prepared)
    stage(prepared, archive, digest)
    return prepared, archive, raw, digest


def prepared_news_rules_fixture(root: Path, *, mismatch: bool = True,
                                same_event: bool = False) -> tuple[Path, Path, Path, str]:
    prepared, archive, raw = (root / name for name in
                              ("prepared.sqlite3", "archive.sqlite3", "raw.sqlite3"))
    with closing(sqlite3.connect(prepared)) as db:
        db.execute("CREATE TABLE prepared_news (scope TEXT,stock_code TEXT,"
                   "identity TEXT,state TEXT,article_json TEXT,body_json TEXT,"
                   "rules_json TEXT,error TEXT,updated_at REAL,"
                   "PRIMARY KEY(scope,stock_code,identity))")
        for identity, title in (("match", "기업, 삼성전자와 500억원 공급계약 체결"),
                                ("mismatch", "기업, 삼성전자와 500억원 공급계약 체결"
                                 if same_event else "일반 기업 소식")):
            document = {"stock_code": "005930", "stock_name": "기업", "identity": identity,
                        "title": title, "description": "", "published_at": "2026-09-01T00:00:00+00:00"}
            article = {"stock_code": "005930", "identity": identity,
                       "document": document, "targets": [["005930", "기업"]]}
            body = {"body_status": "summary_only", "body_text": "기사 요약",
                    "fetched_at": 1790300000.0}
            computed = prepare_job(
                {"job_key": "fixture", "attempts": 1, "stage": "RULE", "target_id": "005930",
                 "payload": {"stock_code": "005930", "stock_name": "기업"}},
                article, {"body_text": body["body_text"], "status": "summary_only"},
                allow_network=False,
            )
            if identity == "mismatch" and mismatch:
                computed["assessment"]["reason"] = "stale prepared assessment"
            rules = [{"target_id": "005930", "stock_name": "기업",
                      "assessment": computed["assessment"],
                      "core_sentences": computed["core_sentences"],
                      "rule_result": computed["rule_result"]}]
            db.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)",
                       ("historical_backfill", "005930", identity, "ready",
                        json.dumps(article), json.dumps(body), json.dumps(rules), "", 1.0))
        db.commit()
    with closing(sqlite3.connect(raw)) as db:
        db.execute("CREATE TABLE news_article_body_snapshots ("
                   "provider TEXT,office_id TEXT,article_id TEXT,"
                   "extractor_version TEXT,body_sha256 TEXT,fetched_at TEXT)")
    SQLiteQueryStore(archive).initialize()
    with closing(sqlite3.connect(archive)) as db:
        db.execute("CREATE TABLE archive_build_manifest (key TEXT PRIMARY KEY,value_json TEXT)")
        db.execute("INSERT INTO archive_build_manifest VALUES('report',?)",
                   (json.dumps({"build_state": "building"}),))
        db.commit()
    digest = sha256_file(prepared)
    stage(prepared, archive, digest)
    finalize_bodies(prepared, archive, raw, digest, 0)
    finalize_assessments(prepared, archive, digest)
    return prepared, archive, raw, digest


def historical_news_archive_fixture(root: Path) -> Path:
    prepared, archive, _, digest = prepared_news_rules_fixture(root, mismatch=False)
    with closing(sqlite3.connect(archive)) as db:
        db.execute("CREATE TABLE archive_seed_roles (table_name TEXT,row_key TEXT,"
                   "payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))")
        db.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,"
            "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("seed-support", "005930", "support", "seed-hash", "seed",
             "2026-08-01T00:00:00+00:00", 1.0, 1.0, "live", None,
             json.dumps({"title": "support reference", "identity": "support"})),
        )
        db.execute("INSERT INTO archive_seed_roles VALUES(?,?,?,?)",
                   ("central_news_article_revisions", '["seed-support"]', "seed-hash",
                    "support_only"))
        db.commit()
    verify(prepared, archive, digest)
    finalize_events(prepared, archive, digest)
    build_search_projection(archive)
    return archive


def seal_historical_news_archive_fixture(archive: Path, dataset_id: str = "fixture-1") -> None:
    with closing(sqlite3.connect(archive)) as db:
        report = json.loads(db.execute(
            "SELECT value_json FROM archive_build_manifest WHERE key='report'"
        ).fetchone()[0])
        report.update({"build_state": "sealed", "dataset_id": dataset_id})
        db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",
                   (json.dumps(report),))
        db.commit()
