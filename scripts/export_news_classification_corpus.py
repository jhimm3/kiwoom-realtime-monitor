"""NAS 뉴스 전체 이력을 읽기 전용으로 내려받아 로컬 평가 SQLite를 만든다."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


HISTORY_KINDS = ("article", "body", "ai", "event", "membership")
CONTENT_COLLECTIONS = ("news_article", "news_watchlist", "stock_fundamentals")
REVISION_KEYS = {
    "article": "article_revision_id",
    "body": "body_revision_id",
    "ai": "analysis_revision_id",
    "event": "event_revision_id",
    "membership": "membership_revision_id",
}


class NewsCorpusExporter:
    def __init__(self, server_url: str, token: str, *, page_size: int = 1000) -> None:
        self._server_url = server_url.rstrip("/")
        self._token = token
        self._page_size = max(1, min(1000, int(page_size)))

    def get_json(self, path: str, parameters: dict[str, object] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(parameters)}" if parameters else ""
        request = Request(
            f"{self._server_url}{path}{query}",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        with urlopen(request, timeout=60) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"JSON object가 아닌 응답입니다: {path}")
        return value

    def history(self, kind: str) -> list[dict[str, Any]]:
        if kind not in REVISION_KEYS:
            raise ValueError(f"지원하지 않는 history kind: {kind}")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        as_of: float | None = None
        while True:
            parameters: dict[str, object] = {"limit": self._page_size}
            if as_of is not None:
                parameters["as_of"] = as_of
            page = self.get_json(f"/api/v1/news/history/{kind}", parameters)
            rows = page.get("revisions", [])
            if not isinstance(rows, list) or not rows:
                break
            added = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                identity = str(row.get(REVISION_KEYS[kind]) or "")
                if identity and identity not in seen:
                    seen.add(identity)
                    result.append(row)
                    added += 1
            available = [
                float(row["available_at"])
                for row in rows
                if isinstance(row, dict) and row.get("available_at") is not None
            ]
            if len(rows) < self._page_size or not available:
                break
            next_as_of = math.nextafter(min(available), -math.inf)
            if added == 0 or (as_of is not None and next_as_of >= as_of):
                raise RuntimeError(f"{kind} history 페이지가 진행되지 않습니다.")
            as_of = next_as_of
        return result

    def content(self, collection: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        offset = 0
        page_size = 10_000
        while True:
            page = self.get_json(
                f"/api/v1/content/{collection}",
                {"limit": page_size, "offset": offset},
            )
            rows = page.get("documents", [])
            if not isinstance(rows, list) or not rows:
                break
            result.extend(row for row in rows if isinstance(row, dict))
            if len(rows) < page_size:
                break
            offset += len(rows)
        return result


def export_corpus(exporter: NewsCorpusExporter, output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    histories = {kind: exporter.history(kind) for kind in HISTORY_KINDS}
    contents = {collection: exporter.content(collection) for collection in CONTENT_COLLECTIONS}
    health = exporter.get_json("/health")
    diagnostics = exporter.get_json("/api/v1/news/sources", {"days": 31, "limit": 1000})
    exported_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(output) as connection:
        connection.executescript(
            "DROP TABLE IF EXISTS records; DROP TABLE IF EXISTS metadata;"
            "CREATE TABLE records("
            " kind TEXT NOT NULL, record_id TEXT NOT NULL, available_at REAL, payload_json TEXT NOT NULL,"
            " PRIMARY KEY(kind,record_id));"
            "CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);"
        )
        for kind, rows in histories.items():
            key = REVISION_KEYS[kind]
            connection.executemany(
                "INSERT INTO records(kind,record_id,available_at,payload_json) VALUES(?,?,?,?)",
                [
                    (
                        f"history:{kind}", str(row[key]), row.get("available_at"),
                        json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                    )
                    for row in rows if row.get(key)
                ],
            )
        for collection, rows in contents.items():
            connection.executemany(
                "INSERT INTO records(kind,record_id,available_at,payload_json) VALUES(?,?,?,?)",
                [
                    (
                        f"content:{collection}", f'{row.get("owner", "")}\0{row.get("key", "")}',
                        row.get("updated_at"), json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                    )
                    for row in rows
                ],
            )
        metadata = {
            "exported_at": exported_at,
            "server_url": exporter._server_url,
            "server_build": health.get("server_build"),
            "counts": {
                **{f"history:{kind}": len(rows) for kind, rows in histories.items()},
                **{f"content:{kind}": len(rows) for kind, rows in contents.items()},
            },
            "news_diagnostics": diagnostics,
        }
        connection.executemany(
            "INSERT INTO metadata(key,value_json) VALUES(?,?)",
            [(key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))) for key, value in metadata.items()],
        )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {**metadata, "output": str(output.resolve()), "sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://192.168.0.5:8787")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--page-size", type=int, default=1000)
    arguments = parser.parse_args()
    token = os.environ.get("MONITOR_SERVER_ACCESS_TOKEN", "").strip()
    if not token:
        parser.error("MONITOR_SERVER_ACCESS_TOKEN 환경 변수가 필요합니다.")
    manifest = export_corpus(
        NewsCorpusExporter(arguments.server_url, token, page_size=arguments.page_size),
        arguments.output,
    )
    # 원문과 비밀값은 출력하지 않는다.
    print(json.dumps({key: value for key, value in manifest.items() if key != "news_diagnostics"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
