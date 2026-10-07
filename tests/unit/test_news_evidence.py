from __future__ import annotations

import unittest
from datetime import UTC, datetime

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.presentation.news_workers import NewsEvidenceWorker, load_stored_news_evidence
from kiwoom_monitor.presentation.news_view_model import stored_news_core_sentences_html


def _item(description: str = "검색 요약") -> StockNewsItem:
    return StockNewsItem(
        "테스트기업 공급계약", description,
        "https://news.example/a?x=1&y=2", "https://origin.example/a?x=1&y=2",
        datetime(2026, 9, 12, tzinfo=UTC),
        assess_stock_news("테스트기업", "테스트기업 공급계약", description),
    )


class _Client:
    def __init__(self, values: dict[tuple[str, str, str], list[dict]],
                 assessments: dict[str, dict] | None = None) -> None:
        self.values = values
        self.assessments = assessments or {}
        self.calls: list[tuple[str, str, str, int]] = []

    def load_news_history(self, kind: str, *, target: str = "", identity: str = "",
                          limit: int = 100, **_kwargs) -> dict:
        self.calls.append((kind, target, identity, limit))
        result = {"known": True, "revisions": self.values.get((kind, target, identity), [])}
        if kind == "body":
            result["assessment"] = self.assessments.get(target)
        return result


class NewsEvidenceTests(unittest.TestCase):
    def test_worker_retries_once_and_old_server_404_falls_back_without_error(self) -> None:
        item = _item()

        class RetryClient:
            def __init__(self): self.calls = 0
            def load_news_history(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("중앙 자료 서버에 연결할 수 없습니다.")
                return {"known": False, "revisions": []}

        retry = RetryClient()
        retried: list[object] = []
        worker = NewsEvidenceWorker(1, "005930", item, retry)  # type: ignore[arg-type]
        worker.completed.connect(lambda *_args: retried.append(_args[-1]))
        worker.run()
        self.assertEqual(3, retry.calls)  # 첫 실패 + 재시도에서 target/GLOBAL 확인
        self.assertEqual("저장 자료 없음", retried[0].notice)

        class OldServer:
            def __init__(self): self.calls = 0
            def load_news_history(self, *_args, **_kwargs):
                self.calls += 1
                raise RuntimeError("중앙 자료 서버 오류: HTTP 404")

        old = OldServer()
        fallback: list[object] = []
        worker = NewsEvidenceWorker(2, "005930", item, old)  # type: ignore[arg-type]
        worker.completed.connect(lambda *_args: fallback.append(_args[-1]))
        worker.run()
        self.assertEqual(1, old.calls)
        self.assertIn("구버전 중앙 서버", fallback[0].notice)

    def test_joins_only_exact_article_body_membership_and_stock_event_revisions(self) -> None:
        item = _item()
        identity = item.original_link
        article = {
            "article_revision_id": "article-r1", "stock_code": "005930", "identity": identity,
            "document": {"title": item.title, "description": item.description},
        }
        body = {
            "body_revision_id": "body-r1", "article_revision_id": "article-r1",
            "status": "fulltext", "body_text": "저장 본문", "error": "",
        }
        membership = {
            "event_revision_id": "event-r1", "event_id": "event-1",
            "article_revision_id": "article-r1", "body_revision_id": "body-r1",
        }
        event = {
            "event_revision_id": "event-r1", "stock_code": "005930",
            "article_revision_id": "article-r1", "body_revision_id": "body-r1",
            "certainty": "CONFIRMED", "result": {},
        }
        wrong_event = {**event, "event_revision_id": "wrong", "stock_code": "000660"}
        client = _Client({
            ("article", "005930", identity): [article],
            ("body", "article-r1", ""): [body],
            ("membership", "", "article-r1"): [membership],
            ("event", "005930", "event-1"): [wrong_event, event],
        }, {"article-r1": {
            "article_revision_id": "article-r1", "body_revision_id": "body-r1",
            "core_sentences": ["NAS가 저장한 핵심 문장"],
        }})

        evidence = load_stored_news_evidence(client, "005930", item)  # type: ignore[arg-type]

        self.assertEqual("article-r1", evidence.article_revision_id)
        self.assertEqual("body-r1", evidence.body_revision_id)
        self.assertEqual("저장 본문", evidence.body_text)
        self.assertEqual(("NAS가 저장한 핵심 문장",), evidence.core_sentences)
        self.assertIn("NAS가 저장한 핵심 문장", stored_news_core_sentences_html(evidence))
        self.assertEqual("event-r1", evidence.event["event_revision_id"] if evidence.event else None)

    def test_ignores_core_sentences_from_another_body_revision(self) -> None:
        item = _item()
        client = _Client({
            ("article", "005930", item.original_link): [{
                "article_revision_id": "article-r1", "stock_code": "005930",
                "identity": item.original_link,
                "document": {"title": item.title, "description": item.description},
            }],
            ("body", "article-r1", ""): [{
                "body_revision_id": "body-r2", "article_revision_id": "article-r1",
                "status": "fulltext", "body_text": "새 본문", "error": "",
            }],
        }, {"article-r1": {
            "article_revision_id": "article-r1", "body_revision_id": "body-r1",
            "core_sentences": ["오래된 본문 요약"],
        }})

        evidence = load_stored_news_evidence(client, "005930", item)  # type: ignore[arg-type]

        self.assertEqual("body-r2", evidence.body_revision_id)
        self.assertEqual((), evidence.core_sentences)

    def test_global_body_is_reused_but_other_stock_event_is_not_mixed(self) -> None:
        item = _item()
        identity = item.original_link
        latest_other = {
            "article_revision_id": "article-r2", "stock_code": "GLOBAL", "identity": identity,
            "document": {"title": item.title, "description": "다른 검색어 요약"},
        }
        matching_old = {
            "article_revision_id": "article-r1", "stock_code": "GLOBAL", "identity": identity,
            "document": {"title": item.title, "description": item.description},
        }
        body = {
            "body_revision_id": "body-r1", "article_revision_id": "article-r1",
            "status": "summary_only", "body_text": "검색 요약", "error": "",
        }
        membership = {
            "event_revision_id": "event-r1", "event_id": "event-1",
            "article_revision_id": "article-r1", "body_revision_id": "body-r1",
        }
        client = _Client({
            ("article", "005930", identity): [],
            ("article", "GLOBAL", identity): [latest_other, matching_old],
            ("body", "article-r1", ""): [body],
            ("membership", "", "article-r1"): [membership],
            ("event", "005930", "event-1"): [{
                "event_revision_id": "event-r1", "stock_code": "000660",
                "article_revision_id": "article-r1", "body_revision_id": "body-r1",
            }],
        })

        evidence = load_stored_news_evidence(client, "005930", item)  # type: ignore[arg-type]

        self.assertTrue(evidence.historical_revision)
        self.assertEqual("summary_only", evidence.body_status)
        self.assertIsNone(evidence.event)
        self.assertIn("GLOBAL 저장 본문", evidence.notice)

    def test_incomplete_target_revision_falls_back_to_exact_global_completed_body(self) -> None:
        item = _item()
        identity = item.original_link
        target_article = {
            "article_revision_id": "target-r1", "stock_code": "005930", "identity": identity,
            "document": {"title": item.title, "description": item.description},
        }
        global_article = {
            "article_revision_id": "global-r1", "stock_code": "GLOBAL", "identity": identity,
            "document": {"title": item.title, "description": item.description},
        }
        client = _Client({
            ("article", "005930", identity): [target_article],
            ("body", "target-r1", ""): [],
            ("article", "GLOBAL", identity): [global_article],
            ("body", "global-r1", ""): [{
                "body_revision_id": "global-body-r1", "article_revision_id": "global-r1",
                "status": "fulltext", "body_text": "GLOBAL 저장 본문", "error": "",
            }],
            ("membership", "", "global-r1"): [],
        })

        evidence = load_stored_news_evidence(client, "005930", item)  # type: ignore[arg-type]

        self.assertEqual("global-r1", evidence.article_revision_id)
        self.assertEqual("global-body-r1", evidence.body_revision_id)
        self.assertEqual("GLOBAL 저장 본문", evidence.body_text)
        self.assertIn("GLOBAL 저장 본문", evidence.notice)

    def test_different_title_or_description_excludes_stored_judgment(self) -> None:
        item = _item()
        identity = item.original_link
        client = _Client({
            ("article", "005930", identity): [{
                "article_revision_id": "other", "stock_code": "005930", "identity": identity,
                "document": {"title": item.title, "description": "정정된 설명"},
            }],
            ("article", "GLOBAL", identity): [],
        })

        evidence = load_stored_news_evidence(client, "005930", item)  # type: ignore[arg-type]

        self.assertEqual("", evidence.article_revision_id)
        self.assertIn("다른 제목·요약 판본", evidence.notice)
        self.assertFalse(any(call[0] == "body" for call in client.calls))

    def test_failed_body_never_loads_or_attaches_rule_result(self) -> None:
        item = _item()
        identity = item.original_link
        client = _Client({
            ("article", "005930", identity): [{
                "article_revision_id": "article-r1", "stock_code": "005930", "identity": identity,
                "document": {"title": item.title, "description": item.description},
            }],
            ("body", "article-r1", ""): [{
                "body_revision_id": "body-r1", "article_revision_id": "article-r1",
                "status": "failed", "body_text": "", "error": "timeout <unsafe>",
            }],
        })

        evidence = load_stored_news_evidence(client, "005930", item)  # type: ignore[arg-type]

        self.assertEqual("failed", evidence.body_status)
        self.assertEqual("timeout <unsafe>", evidence.body_error)
        self.assertIsNone(evidence.event)
        self.assertFalse(any(call[0] == "membership" for call in client.calls))


if __name__ == "__main__":
    unittest.main()
