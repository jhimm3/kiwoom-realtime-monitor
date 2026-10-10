"""Compare real loopback HTTP responses with the pre-refactor news API contract."""
from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tests" / "fixtures" / "api_contract_baselines" / "news_reads_v1.json"
HTTP_BASELINE = ROOT / "tests" / "fixtures" / "api_contract_baselines" / "news_reads_http_v1.json"
SERVICE_BASELINE = ROOT / "tests" / "fixtures" / "api_contract_baselines" / "news_services_http_v1.json"


@contextmanager
def news_service_contract_app(app_factory, database_path):
    """Isolate the HTTP adapters at their existing service boundary, without network providers."""
    from kiwoom_monitor.central_server.news_service import CentralNewsService
    from kiwoom_monitor.central_server.ai_service import CentralAIService
    news, ai = MagicMock(spec=CentralNewsService), MagicMock(spec=CentralAIService)
    news.search.return_value = [{"identity": "article-1", "title": "저장 뉴스", "body": "본문"}]
    news.stored_page.return_value = {"items": [{"identity": "article-1", "title": "저장 뉴스"}],
                                     "has_next": True, "next_offset": 400}
    ai.analyze.return_value = {"stock_code": "005930", "items": [{"summary": "분석 요약", "score": 80}]}
    settings = CentralServerSettings(f"sqlite:///{database_path}", "private-token",
                                     naver_news_client_id="fixture-id", naver_news_client_secret="fixture-secret",
                                     gemini_api_key="fixture-key", news_history_jobs_enabled=False)
    with patch("kiwoom_monitor.central_server.news_service.CentralNewsService", return_value=news), \
            patch("kiwoom_monitor.central_server.ai_service.CentralAIService", return_value=ai):
        yield app_factory(settings), news, ai
    if news.start.await_count != 1 or news.close.await_count != 1 or ai.close.await_count != 1:
        raise AssertionError("service lifecycle did not start/close exactly once")


async def capture_news_service_http_contract(client, news=None, ai=None):
    from kiwoom_monitor.infrastructure.news_ai import NewsAIProviderError
    query = {"stock_code": "005930", "stock_name": "삼성전자"}
    automation = {"ai_auto_analyze": True, "ai_auto_recent_limit": 7,
                  "ai_provider": "gemini", "ai_model": "fixture-model"}
    analysis = {**query, "provider": "gemini", "model": "fixture-model", "article_count": 3,
                "events": [{"identity": "article-1", "title": "제목", "body": "본문",
                            "body_hash": "body-hash", "articles": [{"link": "https://example.com/1"}]}]}
    requests = [
        ("search-auth", "search", query, 401, False, None),
        ("search-validation", "search", {**query, "stock_code": "bad"}, 422, True, None),
        ("search-options", "search", {**query, **automation, "since": "2026-10-01T09:00:00+09:00"}, 200, True, None),
        ("search-defaults", "search", query, 200, True, None),
        ("search-invalid", "search", query, 400, True, ValueError("SEARCH_INVALID")),
        ("search-unavailable", "search", query, 502, True, RuntimeError("SEARCH_UNAVAILABLE")),
        ("stored-page-auth", "stored-page", query, 401, False, None),
        ("stored-page-validation", "stored-page", {**query, "offset": -1}, 422, True, None),
        ("stored-page-options", "stored-page", {**query, **automation, "offset": 200}, 200, True, None),
        ("stored-page-defaults", "stored-page", query, 200, True, None),
        ("analysis-auth", "analyze", analysis, 401, False, None),
        ("analysis-validation", "analyze", {**analysis, "events": []}, 422, True, None),
        ("analysis-options", "analyze", analysis, 200, True, None),
        ("analysis-defaults", "analyze", {**query, "events": [{"identity": "a", "title": "제목"}]}, 200, True, None),
        ("analysis-invalid", "analyze", analysis, 400, True, ValueError("ANALYSIS_INVALID")),
        ("analysis-rate-limit", "analyze", analysis, 429, True, NewsAIProviderError(429)),
        ("analysis-provider-unavailable", "analyze", analysis, 503, True, NewsAIProviderError(503)),
        ("analysis-unavailable", "analyze", analysis, 502, True, RuntimeError("ANALYSIS_UNAVAILABLE")),
    ] if news is not None else [
        ("search-unconfigured", "search", query, 503, True, None),
        ("stored-page-unconfigured", "stored-page", query, 503, True, None),
        ("analysis-unconfigured", "analyze", analysis, 503, True, None),
    ]

    def serializable(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: serializable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [serializable(item) for item in value]
        return value

    cases = []
    for name, endpoint, body, status, authenticated, error in requests:
        method = ({"search": news.search, "stored-page": news.stored_page,
                   "analyze": ai.analyze}[endpoint] if news is not None else None)
        if method is not None:
            method.reset_mock()
            method.side_effect = error
        headers = {"Authorization": "Bearer private-token"} if authenticated else {}
        response = await client.post(f"/api/v1/news/{endpoint}", json=body, headers=headers)
        if response.status_code != status:
            raise AssertionError((name, response.status_code, response.text))
        calls = [{"args": serializable(call.args), "kwargs": serializable(call.kwargs)}
                 for call in method.await_args_list] if method is not None else []
        if len(calls) != (int(status not in (401, 422)) if method is not None else 0):
            raise AssertionError((name, "unexpected service invocation", calls))
        cases.append({"name": name, "request": {"body": body, "path": f"/api/v1/news/{endpoint}",
                                                 "authenticated": authenticated},
                      "status": response.status_code,
                      "headers": {key: response.headers[key] for key in ("content-type", "content-length")},
                      "body": response.json(), "service_calls": calls})
    return cases


async def _capture_service_asgi():
    import httpx
    cases = []
    with tempfile.TemporaryDirectory(prefix="news-service-contract-") as directory:
        with news_service_contract_app(create_app, Path(directory) / "configured.sqlite3") as (app, news, ai):
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://contract.test", timeout=5) as client:
                    cases.extend(await capture_news_service_http_contract(client, news, ai))
        app = create_app(CentralServerSettings(f"sqlite:///{Path(directory) / 'unconfigured.sqlite3'}", "private-token"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://contract.test", timeout=5) as client:
                cases.extend(await capture_news_service_http_contract(client))
    return cases
FIXTURE_TOKEN = "news-contract-fixture-token"
ARTICLE_ID = "news-contract-article-001"
BODY_ID = "news-contract-body-001"


def _requests() -> list[dict[str, object]]:
    authorized = {"authorization": "Bearer <fixture-token>"}
    routes = [
        "/api/v1/news/history/article?target=005930&identity=fixture-article&as_of=250&limit=5",
        f"/api/v1/news/history/body?target={ARTICLE_ID}&stock_code=005930&limit=5",
        "/api/v1/news/sources?source_id=missing-fixture-source&days=31&limit=20",
        "/api/v1/news/market-feed?source=flash&limit=20",
    ]
    values = [
        {"name": "history-article-200", "method": "GET", "url": routes[0], "headers": authorized},
        {"name": "history-body-assessment-200", "method": "GET", "url": routes[1], "headers": authorized},
        {"name": "sources-200", "method": "GET", "url": routes[2], "headers": authorized},
        {"name": "market-feed-200", "method": "GET", "url": routes[3], "headers": authorized},
    ]
    values.extend(
        {"name": f"{name}-401", "method": "GET", "url": path, "headers": {}}
        for name, path in zip(("history", "sources", "market-feed"), routes[:3], strict=True)
    )
    values.extend([
        {"name": "unsupported-history-kind-404", "method": "GET",
         "url": "/api/v1/news/history/unsupported?limit=5", "headers": authorized},
        {"name": "history-target-too-long-422", "method": "GET",
         "url": "/api/v1/news/history/article?target=" + "x" * 201, "headers": authorized},
        {"name": "history-negative-as-of-422", "method": "GET",
         "url": "/api/v1/news/history/article?as_of=-1", "headers": authorized},
        {"name": "source-days-too-large-422", "method": "GET",
         "url": "/api/v1/news/sources?days=32", "headers": authorized},
        {"name": "market-feed-limit-too-large-422", "method": "GET",
         "url": "/api/v1/news/market-feed?source=flash&limit=1001", "headers": authorized},
    ])
    return values


def _http_requests() -> list[dict[str, object]]:
    """Cover every authenticated read route plus the saved status/error cases."""
    values = _requests()
    values[4]["name"] = "history-article-401"
    values[5]["name"] = "history-body-401"
    values[6]["name"] = "sources-401"
    values.insert(7, {
        "name": "market-feed-401", "method": "GET",
        "url": "/api/v1/news/market-feed?source=flash&limit=20", "headers": {},
    })
    return values


def _seed_database(path: Path) -> None:
    store = SQLiteQueryStore(path)
    store.initialize()
    with store._connection() as connection:
        connection.execute(
            "INSERT INTO central_news_article_revisions(article_revision_id,stock_code,identity,content_hash,"
            "collector_id,published_at,received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ARTICLE_ID, "005930", "fixture-article", "fixture-article-hash", "fixture-collector",
             "2026-10-08T09:00:00+09:00", 100.0, 100.0, "watchlist", None,
             json.dumps({"title": "계약 기준 기사", "stock_code": "005930",
                         "stock_name": "삼성전자", "link": "https://example.invalid/article-1"},
                        ensure_ascii=False, separators=(",", ":"))),
        )
        connection.execute(
            "INSERT INTO central_news_body_revisions(body_revision_id,article_revision_id,content_hash,"
            "extractor_version,fetched_at,available_at,status,body_text,error) VALUES(?,?,?,?,?,?,?,?,?)",
            (BODY_ID, ARTICLE_ID, "fixture-body-hash", "fixture-extractor-v1", 200.0,
             200.0, "fulltext", "기준선용 고정 본문입니다.", ""),
        )
        connection.execute(
            "INSERT INTO central_news_source_observations(observation_id,run_id,source_id,query_text,page_start,"
            "article_revision_id,identity,published_at,received_at,available_at,content_hash,duplicate,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("news-contract-observation-001", "news-contract-run-001",
             "naver-stock:flash:2026-10-08", "fixture-query", 1, ARTICLE_ID,
             "https://example.invalid/flash-1", "2026-10-08T09:00:00+09:00", 100.0,
             100.0, "fixture-market-hash", 0,
             json.dumps({"title": "고정 속보", "description": "기준선 저장 뉴스",
                         "link": "https://example.invalid/flash-1",
                         "original_link": "https://example.invalid/original-1",
                         "published_at": "2026-10-08T09:00:00+09:00",
                         "query_membership": "fixture-query"},
                        ensure_ascii=False, separators=(",", ":"))),
        )
    store.upsert_documents("news_assessment", [{
        "owner": "005930", "key": ARTICLE_ID,
        "document": {"category": "supply_contract", "confidence": 0.91,
                     "summary": "고정된 본문 평가"},
    }])
    store.close()


def _database_snapshot(path: Path) -> tuple[str, ...]:
    database_uri = f"file:{path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        return tuple(connection.iterdump())


def _settings(database_path: Path) -> CentralServerSettings:
    return CentralServerSettings(
        database_url=f"sqlite:///{database_path.as_posix()}", access_token=FIXTURE_TOKEN,
        news_naver_api_enabled=False, news_naver_stock_enabled=False,
        news_naver_market_enabled=False, news_history_jobs_enabled=False,
        news_query_set_enabled=False, autonomous_top20_enabled=False,
        autonomous_top20_minute_backfill_enabled=False, market_event_collection_enabled=False,
        external_market_enabled=False, shadow_candidate_enabled=False,
    )


async def _capture_async() -> dict[str, object]:
    import httpx

    with tempfile.TemporaryDirectory(prefix="news-api-baseline-") as directory:
        database_path = Path(directory) / "news-fixture.sqlite3"
        _seed_database(database_path)
        app = create_app(_settings(database_path))
        # Preserve the original ASGI baseline alongside the loopback HTTP baseline.
        async with app.router.lifespan_context(app):
            before = _database_snapshot(database_path)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://contract.test", timeout=5,
            ) as client:
                results = []
                for case in _requests():
                    headers = dict(case["headers"])
                    if headers.get("authorization") == "Bearer <fixture-token>":
                        headers.pop("authorization")
                        headers["Authorization"] = f"Bearer {FIXTURE_TOKEN}"
                    response = await client.request(str(case["method"]), str(case["url"]), headers=headers)
                    results.append({
                        "name": case["name"],
                        "request": {"method": case["method"], "url": case["url"],
                                    "headers": case["headers"]},
                        "response": {
                            "status": response.status_code,
                            "headers": {name: response.headers[name] for name in
                                        ("content-type", "content-length") if name in response.headers},
                            "body": response.json(),
                        },
                    })
                if before != _database_snapshot(database_path):
                    raise AssertionError("ASGI news read requests changed the SQLite fixture")
                return {"schema_version": 1, "transport": "httpx-asgi-with-lifespan",
                        "database": "disposable-seeded-sqlite", "cases": results}


def _capture_asgi() -> dict[str, object]:
    return asyncio.run(_capture_async())


def _capture_http() -> dict[str, object]:
    import httpx
    import uvicorn

    with tempfile.TemporaryDirectory(prefix="news-api-http-baseline-") as directory:
        database_path = Path(directory) / "news-fixture.sqlite3"
        _seed_database(database_path)
        app = create_app(_settings(database_path))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            server = uvicorn.Server(uvicorn.Config(
                app, host=None, port=None, log_level="critical", lifespan="on",
            ))
            errors: list[BaseException] = []

            def serve() -> None:
                try:
                    server.run(sockets=[listener])
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=serve, name="news-api-contract-server", daemon=False)
            thread.start()
            try:
                deadline = time.monotonic() + 30
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    thread.join(0.02)
                if errors:
                    raise RuntimeError("Uvicorn failed during startup") from errors[0]
                if not server.started:
                    raise TimeoutError("Uvicorn did not complete application startup within 30 seconds")

                before = _database_snapshot(database_path)
                results = []
                with httpx.Client(base_url=base_url, timeout=10) as client:
                    for case in _http_requests():
                        headers = dict(case["headers"])
                        if headers.get("authorization") == "Bearer <fixture-token>":
                            headers.pop("authorization")
                            headers["Authorization"] = f"Bearer {FIXTURE_TOKEN}"
                        response = client.request(str(case["method"]), str(case["url"]), headers=headers)
                        results.append({
                            "name": case["name"],
                            "request": {"method": case["method"], "url": case["url"],
                                        "headers": case["headers"]},
                            "response": {
                                "status": response.status_code,
                                "headers": {name: response.headers[name] for name in
                                            ("content-type", "content-length") if name in response.headers},
                                "body": response.json(),
                            },
                        })
                if before != _database_snapshot(database_path):
                    raise AssertionError("HTTP news read requests changed the SQLite fixture")
                return {"schema_version": 1, "transport": "uvicorn-loopback-http-with-lifespan",
                        "database": "disposable-seeded-sqlite", "cases": results}
            finally:
                server.should_exit = True
                thread.join(15)
                if thread.is_alive():
                    raise TimeoutError("Uvicorn test server did not stop within 15 seconds")
                if errors:
                    raise RuntimeError("Uvicorn failed while serving HTTP requests") from errors[0]


class NewsApiContractBaselineTests(unittest.TestCase):
    def test_service_http_contract_and_arguments_match_pre_extraction_baseline(self) -> None:
        expected = json.loads(SERVICE_BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(expected["cases"], asyncio.run(_capture_service_asgi()))

    def test_same_asgi_requests_match_preserved_pre_refactor_responses(self) -> None:
        expected = json.loads(BASELINE.read_text(encoding="utf-8"))
        actual = _capture_asgi()
        self.assertEqual(expected, actual)

    def test_loopback_http_matches_preserved_pre_refactor_responses(self) -> None:
        expected = json.loads(HTTP_BASELINE.read_text(encoding="utf-8"))
        actual = _capture_http()
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--capture-http"]:
        if HTTP_BASELINE.exists():
            raise SystemExit(f"Refusing to replace existing API baseline: {HTTP_BASELINE}")
        HTTP_BASELINE.parent.mkdir(parents=True, exist_ok=True)
        HTTP_BASELINE.write_text(
            json.dumps(_capture_http(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        print(f"Captured pre-refactor loopback HTTP responses: {HTTP_BASELINE}")
    else:
        unittest.main()
