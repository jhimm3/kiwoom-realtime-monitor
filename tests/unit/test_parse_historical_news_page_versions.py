from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.parse_historical_news_page_versions import parse_pages
from scripts.audit_historical_news_page_versions import audit


def _payload() -> str:
    article = {
        "content": "기사 요약", "contentHref": "https://example.test/original/1",
        "sourceProfile": {"title": "매체"}, "title": "충분한 길이의 기사 제목",
    }
    bootstrap = {"body": {"props": {"children": [{"props": article}]}}}
    return json.dumps({"collection": [{"script":
        "entry.bootstrap(document.getElementById(\"root\"), "
        + json.dumps(bootstrap, ensure_ascii=False) + ");"}]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class HistoricalNewsPageVersionsTests(unittest.TestCase):
    def test_failed_initialization_can_restart_without_partial_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, prepared, keys, output = (
                root / name for name in ("raw.sqlite3", "prepared.sqlite3",
                                         "keys.sqlite3", "parsed.sqlite3"))
            payload = '{"collection":[]}'
            request_key = "code=004770&date=2020-01-02&query=probe&start=1"
            with closing(sqlite3.connect(raw)) as connection:
                connection.execute("CREATE TABLE source_pages (provider TEXT,request_key TEXT,"
                                   "observed_at TEXT,response_sha256 TEXT,item_count INTEGER,"
                                   "payload_json TEXT)")
                connection.execute("INSERT INTO source_pages VALUES(?,?,?,?,?,?)",
                                   ("naver_historical_search", request_key,
                                    "2026-09-22T05:00:00+00:00",
                                    hashlib.sha256(payload.encode()).hexdigest(), 0, payload))
                connection.commit()
            with closing(sqlite3.connect(prepared)) as connection:
                connection.execute("CREATE TABLE prepared_news (scope TEXT,stock_code TEXT,"
                                   "identity TEXT,state TEXT)")
                connection.commit()
            manifest = {"schema": "historical-news-page-keys/v2",
                        "raw_path": str(raw.resolve()),
                        "prepared_path": str(prepared.resolve()),
                        "source_pages_max_rowid": 1, "page_versions": 2}
            with closing(sqlite3.connect(keys)) as connection:
                connection.execute("CREATE TABLE selected_request_keys ("
                                   "request_key TEXT PRIMARY KEY,observation_count INTEGER)")
                connection.execute("CREATE TABLE input_manifest ("
                                   "key TEXT PRIMARY KEY,value_json TEXT)")
                connection.execute("INSERT INTO selected_request_keys VALUES(?,1)",
                                   (request_key,))
                connection.execute("INSERT INTO input_manifest VALUES('report',?)",
                                   (json.dumps(manifest),))
                connection.commit()
            with self.assertRaisesRegex(ValueError, "page count changed"):
                parse_pages(raw, prepared, keys, output, max_pages=1)
            with closing(sqlite3.connect(output)) as connection:
                self.assertEqual([], connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
            manifest["page_versions"] = 1
            with closing(sqlite3.connect(keys)) as connection:
                connection.execute("UPDATE input_manifest SET value_json=? WHERE key='report'",
                                   (json.dumps(manifest),))
                connection.commit()
            self.assertEqual({"parsed": 1},
                             parse_pages(raw, prepared, keys, output,
                                         max_pages=1)["states"])

    def test_resume_preserves_parsed_rows_and_records_bad_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, prepared, keys, output = (
                root / name for name in ("raw.sqlite3", "prepared.sqlite3",
                                         "keys.sqlite3", "parsed.sqlite3"))
            request_key = "code=004770&from=2020-01-02&to=2020-01-03&query=C&C&start=1"
            good = _payload()
            bad = json.dumps({"collection": "wrong"})
            with closing(sqlite3.connect(raw)) as connection:
                connection.execute("CREATE TABLE source_pages ("
                                   "provider TEXT,request_key TEXT,observed_at TEXT,"
                                   "response_sha256 TEXT,item_count INTEGER,payload_json TEXT)")
                connection.executemany(
                    "INSERT INTO source_pages VALUES(?,?,?,?,?,?)",
                    [("naver_historical_search", request_key,
                      f"2026-09-22T05:0{index}:00+00:00",
                      hashlib.sha256(payload.encode()).hexdigest(), 1, payload)
                     for index, payload in enumerate((good, bad))],
                )
                connection.commit()
            with closing(sqlite3.connect(prepared)) as connection:
                connection.execute("CREATE TABLE prepared_news ("
                                   "scope TEXT,stock_code TEXT,identity TEXT,state TEXT)")
                connection.execute("INSERT INTO prepared_news VALUES(?,?,?,?)",
                                   ("historical_backfill", "004770",
                                    "https://example.test/original/1", "ready"))
                connection.commit()
            with closing(sqlite3.connect(keys)) as connection:
                connection.execute("CREATE TABLE selected_request_keys ("
                                   "request_key TEXT PRIMARY KEY,observation_count INTEGER)")
                connection.execute("CREATE TABLE input_manifest ("
                                   "key TEXT PRIMARY KEY,value_json TEXT)")
                connection.execute("INSERT INTO selected_request_keys VALUES(?,2)",
                                   (request_key,))
                manifest = {"schema": "historical-news-page-keys/v2",
                            "raw_path": str(raw.resolve()),
                            "prepared_path": str(prepared.resolve()),
                            "source_pages_max_rowid": 2, "page_versions": 2}
                connection.execute("INSERT INTO input_manifest VALUES('report',?)",
                                   (json.dumps(manifest),))
                connection.commit()

            first = parse_pages(raw, prepared, keys, output, max_pages=1)
            self.assertEqual({"parsed": 1, "pending": 1}, first["states"])
            self.assertEqual(1, first["prepared_matches"])
            second = parse_pages(raw, prepared, keys, output, max_pages=1)
            self.assertEqual({"parsed": 1, "error": 1}, second["states"])
            self.assertEqual("incomplete_errors", second["build_state"])
            coverage = audit(output, prepared, keys, deep=True)
            self.assertEqual(1, coverage["prepared_coverage"][0]["prepared_rows"])
            self.assertEqual(0, coverage["prepared_coverage"][0]["without_page_observation"])
            self.assertEqual("incomplete", coverage["coverage_state"])
            self.assertEqual(1, coverage["variants"]["request_keys_with_changed_response_hash"])
            self.assertEqual(0, coverage["variants"]["identities_with_changed_page_item_excluding_position"])
            again = parse_pages(raw, prepared, keys, output, max_pages=1)
            self.assertEqual(0, again["processed_now"])
            with closing(sqlite3.connect(output)) as connection:
                self.assertEqual(1, connection.execute(
                    "SELECT COUNT(*) FROM page_article_observations").fetchone()[0])
                self.assertEqual(("004770", "https://example.test/original/1"),
                                 connection.execute(
                                     "SELECT stock_code,identity FROM page_article_observations"
                                 ).fetchone())

            with closing(sqlite3.connect(prepared)) as connection:
                connection.execute("UPDATE prepared_news SET identity='changed'")
                connection.commit()
            with self.assertRaisesRegex(ValueError, "input changed"):
                parse_pages(raw, prepared, keys, output, max_pages=1)


if __name__ == "__main__":
    unittest.main()
