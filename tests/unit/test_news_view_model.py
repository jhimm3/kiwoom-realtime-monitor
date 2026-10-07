from __future__ import annotations

import unittest
from datetime import UTC, datetime

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.application.news_grouping import NewsEventGroup
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import StoredAINewsAnalysis
from kiwoom_monitor.presentation.news_view_model import (
    StoredNewsEvidence,
    ai_detail_html,
    build_display_row,
    effective_judgment,
    related_articles_html,
    stored_news_core_sentences_html,
    stored_news_evidence_html,
)


def _item(title: str, link: str = "https://example.com") -> StockNewsItem:
    return StockNewsItem(
        title,
        "설명 <확인>",
        link,
        link,
        datetime(2026, 9, 9, 9, 30, tzinfo=UTC),
        assess_stock_news("테스트기업", title, "설명"),
    )


class NewsViewModelTests(unittest.TestCase):
    def test_ai_judgment_and_display_row_keep_existing_labels(self) -> None:
        item = _item("테스트기업 공급계약")
        stored = StoredAINewsAnalysis(
            AINewsAnalysis("요약", "긍정", 87, "계약 체결", ("수주",), (), "수주·계약"),
            "gemini",
            "model",
            datetime.now(UTC),
        )

        category, outlook, reason, source = effective_judgment(item, stored)
        row = build_display_row(item, NewsEventGroup(item, (item,)), stored)

        self.assertEqual("수주·계약", category)
        self.assertEqual("호재 가능성 높음", outlook)
        self.assertEqual("계약 체결", reason)
        self.assertIn("신뢰도 87%", source)
        self.assertEqual("호재 가능성 높음  ᴬᴵ", row.outlook)

    def test_detail_and_related_articles_escape_untrusted_text(self) -> None:
        first = _item("대표 기사")
        second = _item("<script>기사</script>", "https://example.com/?a=1&b=2")
        group = NewsEventGroup(first, (first, second))

        related = related_articles_html(group)
        detail = ai_detail_html(first, None)

        self.assertNotIn("<script>", related)
        self.assertIn("&lt;script&gt;", related)
        self.assertIn("관련 기사 2건", related)
        self.assertEqual("", detail)

    def test_nas_evidence_separates_rule_scores_from_ai_and_escapes_body(self) -> None:
        evidence = StoredNewsEvidence(
            identity="article-1", article_revision_id="article-r1", body_revision_id="body-r1",
            body_status="fulltext", body_text="본문 <script>alert(1)</script>",
            event={
                "certainty": "CONFIRMED", "novelty": "NEW", "amount_won": 50_000_000_000,
                "counterparty": "고객사<1>", "scope": "TARGET_COMPANY", "role": "FACT",
                "importance_score": 80, "confidence_score": 0, "novelty_score": 80,
                "ai_required": True,
                "result": {
                    "ai_reason": ["conditional_amount"],
                    "evidence_spans": [{"field": "body", "text": "<계약>", "fact": "confirmed"}],
                },
            },
        )

        rendered = stored_news_evidence_html(evidence)

        self.assertIn("원문 수집 완료", rendered)
        self.assertIn("계약 확정", rendered)
        self.assertIn("50,000,000,000원", rendered)
        self.assertIn("AI 분석 점수나 AI 신뢰도가 아님", rendered)
        self.assertIn("확신도 0", rendered)
        self.assertIn("조건부 금액", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;계약&gt;", rendered)
        self.assertNotIn("AI 없이 뽑은 핵심 문장", rendered)
        self.assertIn("AI 없이 뽑은 핵심 문장", stored_news_core_sentences_html(evidence))

    def test_non_contract_evidence_does_not_show_empty_contract_rule(self) -> None:
        rendered = stored_news_evidence_html(StoredNewsEvidence(
            identity="a", article_revision_id="ar", body_revision_id="br",
            body_status="fulltext", body_text="테스트기업이 분기 실적을 발표했습니다.",
        ))

        self.assertNotIn("공급계약 규칙", rendered)
        self.assertNotIn("NAS 저장 근거", rendered)
        self.assertIn("기사 원문", rendered)

    def test_nas_evidence_distinguishes_summary_failed_pending_and_historical_version(self) -> None:
        summary = stored_news_evidence_html(StoredNewsEvidence(
            identity="a", article_revision_id="ar", body_revision_id="br",
            body_status="summary_only", body_text="검색 요약", historical_revision=True,
        ))
        failed = stored_news_evidence_html(StoredNewsEvidence(
            identity="a", article_revision_id="ar", body_revision_id="br",
            body_status="failed", body_error="수집 <실패>",
        ))
        pending = stored_news_evidence_html(StoredNewsEvidence(
            identity="a", article_revision_id="ar", notice="본문이 아직 처리 중입니다.",
        ))

        self.assertIn("검색 요약만 저장", summary)
        self.assertIn("저장 당시 판본", summary)
        self.assertIn("본문 수집 실패", failed)
        self.assertIn("수집 &lt;실패&gt;", failed)
        self.assertIn("본문 아직 처리 중", pending)

    def test_nas_evidence_formats_body_into_readable_paragraphs_without_trusting_html(self) -> None:
        body = (
            "첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다. "
            "네 번째 문장에는 <태그>가 있습니다. 다섯 번째 문장입니다."
        )
        rendered = stored_news_evidence_html(StoredNewsEvidence(
            identity="a", article_revision_id="ar", body_revision_id="br",
            body_status="fulltext", body_text=body,
        ))

        self.assertGreaterEqual(rendered.count("line-height:1.75"), 2)
        self.assertIn("&lt;태그&gt;", rendered)
        self.assertNotIn("<태그>", rendered)


if __name__ == "__main__":
    unittest.main()
