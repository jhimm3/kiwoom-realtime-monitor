"""Prepare historical BODY/RULE on this PC without claiming NAS jobs.

Results stay in a resumable local SQLite file until the NAS processed-import
endpoint accepts them. The source archives are read only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION

from scripts.import_historical_market_news_to_nas import _catalog_matcher, _item
from scripts.preprocess_historical_news_to_nas import prepare_job


def _search_article(database: Path, stock_code: str, identity: str) -> dict:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT a.provider,a.office_id,a.article_id,a.title,a.summary,a.published_at,"
            "a.published_precision,a.office_name,a.article_url,a.original_url,a.portal_url,"
            "a.published_at_source,a.publication_source_url,MIN(o.query_text) AS stock_name "
            "FROM news_articles a "
            "JOIN news_search_observations o ON o.provider=a.provider "
            "AND o.office_id=a.office_id AND o.article_id=a.article_id "
            "WHERE o.code=? AND COALESCE(NULLIF(a.original_url,''),"
            "NULLIF(a.portal_url,''),'naver:'||a.office_id||':'||a.article_id)=? "
            "GROUP BY a.provider,a.office_id,a.article_id",
            (stock_code, identity),
        ).fetchone()
    if row is None:
        raise ValueError("검색뉴스 원자료와 NAS 수입 원장 연결이 없습니다.")
    document = {
        "stock_code": stock_code, "stock_name": str(row["stock_name"] or ""),
        "identity": identity, "title": str(row["title"] or ""),
        "description": str(row["summary"] or ""),
        "link": str(row["portal_url"] or row["article_url"] or ""),
        "original_link": str(row["original_url"] or ""),
        "published_at": str(row["published_at"] or ""),
        "publisher_name": str(row["office_name"] or ""),
        "historical_source": {
            "provider": str(row["provider"]), "office_id": str(row["office_id"]),
            "article_id": str(row["article_id"]),
            "published_precision": str(row["published_precision"] or ""),
            "published_at_source": str(row["published_at_source"] or ""),
            "publication_source_url": str(row["publication_source_url"] or ""),
        },
    }
    return {"stock_code": stock_code, "identity": identity, "document": document,
            "targets": [(stock_code, document["stock_name"] or stock_code)]}


def _market_article(database: Path, identity: str, matcher: tuple) -> dict:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT source,office_id,article_id,published_at,title,summary,publisher,article_url "
            "FROM market_news_articles WHERE article_url=? LIMIT 1", (identity,),
        ).fetchone()
    if row is None:
        raise ValueError("시황뉴스 원자료가 없습니다.")
    by_name, pattern = matcher
    item = _item(row, by_name, pattern)
    if item is None or str(item["identity"]) != identity:
        raise ValueError("시황뉴스 원자료 identity가 일치하지 않습니다.")
    targets = [("GLOBAL", "시황")]
    targets.extend((str(target["stock_code"]), str(target.get("stock_name") or target["stock_code"]))
                   for target in item["targets"]
                   if target.get("relation_status") == "confirmed" and target.get("stock_code"))
    return {"stock_code": "GLOBAL", "identity": identity,
            "document": item["document"], "targets": list(dict.fromkeys(targets)),
            "source": str(row["source"]), "target_date": str(row["published_at"])[:10],
            "source_item": item}


def _prepare(scope: str, stock_code: str, identity: str, search_database: Path,
             market_database: Path, matcher: tuple, *,
             allow_network: bool = True) -> tuple[dict, dict, list[dict]]:
    article = (_search_article(search_database, stock_code, identity)
               if scope == "historical_backfill" else
               _market_article(market_database, identity, matcher))
    body = prepare_job({"job_key": "local", "attempts": 1, "stage": "BODY",
                        "processing_version": ARTICLE_BODY_EXTRACTOR_VERSION},
                       article, search_database=search_database if scope == "historical_backfill" else None,
                       allow_network=allow_network)
    body_revision = {"body_text": body["body_text"], "status": body["body_status"]}
    rules = []
    for target_code, target_name in article["targets"]:
        rule = prepare_job({"job_key": "local", "attempts": 1, "stage": "RULE",
                            "target_id": target_code,
                            "payload": {"stock_code": target_code, "stock_name": target_name}},
                           article, body_revision)
        rules.append({"target_id": target_code, "stock_name": target_name,
                      "assessment": rule["assessment"],
                      "core_sentences": rule["core_sentences"],
                      "rule_result": rule["rule_result"]})
    return article, body, rules


def _open_results(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS prepared_news ("
                       "scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,"
                       "state TEXT NOT NULL,article_json TEXT NOT NULL,body_json TEXT NOT NULL,"
                       "rules_json TEXT NOT NULL,error TEXT NOT NULL,updated_at REAL NOT NULL,"
                       "PRIMARY KEY(scope,stock_code,identity))")
    connection.commit()
    return connection


def _write_prepared_rows(output: sqlite3.Connection,
                         rows: list[tuple[str, str, str, dict, dict, list[dict], str]]) -> None:
    if not rows:
        return
    output.executemany(
            "INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(scope,stock_code,identity) DO UPDATE SET "
            "state=excluded.state,article_json=excluded.article_json,"
            "body_json=excluded.body_json,rules_json=excluded.rules_json,"
            "error=excluded.error,updated_at=excluded.updated_at",
            [(scope, code, identity, "failed" if error else "ready",
              json.dumps(article, ensure_ascii=False),
              json.dumps(body, ensure_ascii=False),
              json.dumps(rules, ensure_ascii=False), error[:1000], time.time())
             for scope, code, identity, article, body, rules, error in rows],
    )
    output.commit()


def save_prepared_batch(path: Path, rows: list[tuple[str, str, str, dict, dict, list[dict], str]]) -> None:
    with closing(_open_results(path)) as output:
        _write_prepared_rows(output, rows)


class ConcurrentArticlePreparation:
    """Bounded article-level BODY/RULE work fed by either historical collector."""

    def __init__(self, *, output: Path, search_database: Path,
                 market_database: Path, matcher: tuple, workers: int = 4,
                 allow_network: bool = True) -> None:
        if not 1 <= workers <= 8:
            raise ValueError("workers must be 1..8")
        self.output = output
        self.search_database = search_database
        self.market_database = market_database
        self.matcher = matcher
        self.workers = workers
        self.allow_network = allow_network
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="news-prepare")
        self.pending: dict = {}
        self.queued: set[tuple[str, str, str]] = set()
        self.ready = self.failed = self.skipped = 0

    def submit(self, scope: str, code: str, identity: str) -> None:
        key = (scope, code, identity)
        if key in self.queued:
            return
        with closing(sqlite3.connect(f"file:{self.output.resolve().as_posix()}?mode=ro",
                                     uri=True, timeout=10)) as output:
            if output.execute("SELECT state FROM prepared_news WHERE scope=? AND stock_code=? "
                              "AND identity=?", key).fetchone() == ("ready",):
                self.skipped += 1
                return
        future = self.pool.submit(_prepare, scope, code, identity,
                                  self.search_database, self.market_database,
                                  self.matcher, allow_network=self.allow_network)
        self.pending[future] = key
        self.queued.add(key)
        if len(self.pending) >= self.workers * 4:
            self.drain(wait_for_one=True)

    def drain(self, *, wait_for_one: bool = False, all_pending: bool = False) -> None:
        if not self.pending:
            return
        if all_pending:
            done = list(self.pending)
        elif wait_for_one:
            done = list(wait(self.pending, return_when=FIRST_COMPLETED).done)
        else:
            done = [future for future in self.pending if future.done()]
        rows = []
        for future in done:
            scope, code, identity = self.pending.pop(future)
            self.queued.discard((scope, code, identity))
            try:
                article, body, rules = future.result()
            except Exception as error:
                article, body, rules = {}, {}, []
                detail = f"{type(error).__name__}: {error}"
                self.failed += 1
            else:
                detail = ""
                self.ready += 1
            rows.append((scope, code, identity, article, body, rules, detail))
        save_prepared_batch(self.output, rows)

    def close(self) -> None:
        try:
            self.drain(all_pending=True)
        finally:
            self.pool.shutdown(wait=True)

    def __enter__(self) -> "ConcurrentArticlePreparation":
        with closing(_open_results(self.output)):
            pass
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--search-database", type=Path,
                        default=Path("data/historical_intelligence.sqlite3"))
    parser.add_argument("--market-database", type=Path,
                        default=Path("data/naver_stock_market_news.sqlite3"))
    parser.add_argument("--candidates", type=Path,
                        default=Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3"))
    parser.add_argument("--output", type=Path,
                        default=Path("data/historical_collection/prepared_news.sqlite3"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or args.limit < 0:
        parser.error("workers must be 1..8 and limit must be non-negative")
    manifest = args.manifest.resolve(strict=True)
    search_db = args.search_database.resolve(strict=True)
    market_db = args.market_database.resolve(strict=True)
    matcher = _catalog_matcher(args.candidates.resolve(strict=True))
    with closing(_open_results(args.output)) as output, ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = []
        ready = failed = skipped = 0

        def flush() -> None:
            nonlocal ready, failed
            rows = []
            for scope, code, identity, future in pending:
                try:
                    article, body, rules = future.result()
                    error = ""
                except Exception as exc:
                    article, body, rules = {}, {}, []
                    error = f"{type(exc).__name__}: {exc}"[:1000]
                    failed += 1
                else:
                    ready += 1
                rows.append((scope, code, identity, article, body, rules, error))
            _write_prepared_rows(output, rows)
            pending.clear()
            if (ready + failed) % 100 < args.workers or (args.limit and ready + failed >= args.limit):
                print(json.dumps({"ready": ready, "failed": failed, "skipped": skipped},
                                 ensure_ascii=False), flush=True)

        with manifest.open("r", encoding="utf-8") as source:
            for line in source:
                value = json.loads(line)
                scope, code, identity = value["scope"], value["stock_code"], value["identity"]
                if output.execute("SELECT state FROM prepared_news WHERE scope=? AND stock_code=? "
                                  "AND identity=?", (scope, code, identity)).fetchone() == ("ready",):
                    skipped += 1
                    continue
                pending.append((scope, code, identity,
                                pool.submit(_prepare, scope, code, identity,
                                            search_db, market_db, matcher)))
                if len(pending) >= args.workers:
                    flush()
                if args.limit and ready + failed >= args.limit:
                    break
        if pending:
            flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
