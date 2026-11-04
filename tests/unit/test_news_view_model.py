from __future__ import annotations

import unittest
from datetime import UTC, datetime
from kiwoom_monitor.infrastructure.time_display import format_kst_time

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
    def test_kst_display_preserves_instants_and_date_precision(self):
        utc = datetime(2026, 10, 14, 16, 30, tzinfo=UTC)
        before = utc.timestamp()
        for value in (utc, utc.timestamp(), "2026-10-14T16:30:00Z", "2026-10-15T01:30:00+09:00"):
            with self.subTest(value=value):
                self.assertEqual(format_kst_time(value), "2026-10-15 01:30:00 KST")
        self.assertEqual(utc.timestamp(), before)
        self.assertEqual(format_kst_time("2026-10-15T01:30:00"), "2026-10-15 01:30:00 KST")
        self.assertEqual(format_kst_time("2026-10-15"), "2026-10-15")
        self.assertEqual(format_kst_time(None, missing="미확인"), "미확인")
        self.assertEqual(format_kst_time("invalid"), "invalid")
        # Formatting never silently repairs an incorrectly tagged historical UTC value.
        self.assertEqual(format_kst_time("2026-10-15T14:21:02+00:00"), "2026-10-15 23:21:02 KST")

    def test_news_publication_uses_kst_across_utc_date_boundary(self):
        from dataclasses import replace
        item = replace(_item("시각 확인"), published_at=datetime(2026, 10, 14, 16, 30, tzinfo=UTC))
        row = build_display_row(item, NewsEventGroup(item, (item,)), None)
        self.assertEqual(row.published, "10-15 01:30")

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
        self.assertEqual("호재 가능성 높음  ᴬᴵ · 판본 미확인", row.outlook)

    def _versioned_ai(self):
        from dataclasses import replace
        from kiwoom_monitor.domain.news_ai_contract import freeze_news_ai_input, news_ai_article_input, news_ai_source_member
        from kiwoom_monitor.domain.news_observation import stable_document_hash
        item = replace(_item("판본 기사"), input_source={
            "version": 1, "article_revision_id": "article-1", "body_revision_id": "body-1",
            "body_content_hash": stable_document_hash({"body": "original body"}),
        })
        member = news_ai_source_member(0, news_ai_article_input(item), "original body", "fulltext",
            input_text="original body", span=(0, len("original body")))
        frozen = freeze_news_ai_input(item.link, item.title, "original body", "original-hash", [member], kind="article")
        stored = StoredAINewsAnalysis(AINewsAnalysis("원판단 요약", "긍정", 87, "원판단 근거"),
            "gemini", "model", datetime(2026, 10, 29, tzinfo=UTC), "original-hash",
            {"status": "REPORTED", "input_binding": frozen["input_binding"]})
        return item, stored

    def test_new_body_labels_previous_judgment_without_changing_original_value_or_refs(self):
        from copy import deepcopy
        from dataclasses import replace
        from kiwoom_monitor.domain.news_ai_contract import news_ai_version_status
        from kiwoom_monitor.domain.news_observation import stable_document_hash
        original, stored = self._versioned_ai()
        snapshot = deepcopy(stored.provenance)
        current = replace(original, input_source={
            "version": 1, "article_revision_id": "article-1", "body_revision_id": "body-2",
            "body_content_hash": stable_document_hash({"body": "corrected body"}),
        })
        group = NewsEventGroup(current, (current,))
        self.assertEqual("CURRENT", news_ai_version_status((original,), stored.provenance))
        self.assertEqual("PREVIOUS", news_ai_version_status((current,), stored.provenance))
        self.assertIn("이전 판단", build_display_row(current, group, stored).outlook)
        self.assertIn("이전 판단", effective_judgment(current, stored, group)[3])
        self.assertIn("직접 요청할 때만", ai_detail_html(current, stored, group))
        self.assertIn("원판단 요약", ai_detail_html(current, stored, group))
        self.assertEqual(snapshot, stored.provenance)
        self.assertEqual("원판단 근거", stored.analysis.reason)
        # Reopening onto the original source changes only the derived display.
        self.assertNotIn("이전 판단", build_display_row(original, NewsEventGroup(original, (original,)), stored).outlook)

    def test_bundle_checks_actual_members_and_never_uses_requester_pin_as_produced_body(self):
        from copy import deepcopy
        from dataclasses import replace
        from kiwoom_monitor.domain.news_ai_contract import news_ai_version_status
        original, stored = self._versioned_ai()
        current = replace(original, input_source={**original.input_source, "body_revision_id": "body-2"})
        metadata = deepcopy(stored.provenance)
        metadata.update(article_revision_id="article-1", body_revision_id="body-2")
        self.assertEqual("PREVIOUS", news_ai_version_status((current,), metadata))
        unrelated = replace(original, link="https://example.com/other", original_link="https://example.com/other")
        self.assertEqual("UNKNOWN", news_ai_version_status((original, unrelated), stored.provenance))
        self.assertEqual("UNKNOWN", news_ai_version_status((replace(original, input_source=None),), stored.provenance))
        self.assertEqual("UNKNOWN", news_ai_version_status((original,), {"status": "UNKNOWN"}))

    def test_nonrepresentative_bundle_body_change_keeps_whole_inference_as_previous(self):
        from dataclasses import replace
        from kiwoom_monitor.domain.news_ai_contract import (
            freeze_news_ai_input, news_ai_article_input, news_ai_source_member, news_ai_version_status,
        )
        original, stored = self._versioned_ai()
        second = replace(original, link="https://example.com/second", original_link="https://example.com/second",
            input_source={**original.input_source, "article_revision_id": "article-2", "body_revision_id": "body-2"})
        raw = "original body"
        members = [news_ai_source_member(index, news_ai_article_input(item), raw, "fulltext",
            input_text=raw, span=(index * len(raw), (index + 1) * len(raw)))
            for index, item in enumerate((original, second))]
        frozen = freeze_news_ai_input(original.link, original.title, raw + raw, "bundle-hash", members, kind="bundle")
        stored = replace(stored, provenance={"status": "REPORTED", "input_binding": frozen["input_binding"]})
        changed = replace(second, input_source={**second.input_source, "body_revision_id": "body-3"})
        group = NewsEventGroup(original, (original, changed))
        self.assertEqual("PREVIOUS", news_ai_version_status(group.items, stored.provenance))
        self.assertIn("이전 판단", build_display_row(original, group, stored).outlook)
        self.assertIn("이전 판단", ai_detail_html(original, stored, group))

    def test_detail_and_related_articles_escape_untrusted_text(self) -> None:
        first = _item("대표 기사")
        second = _item("<script>기사</script>", "https://example.com/?a='quoted'&b=2")
        group = NewsEventGroup(first, (first, second))

        related = related_articles_html(group)
        detail = ai_detail_html(first, None)

        self.assertNotIn("<script>", related)
        self.assertIn("&lt;script&gt;", related)
        self.assertIn("관련 기사 2건", related)
        self.assertIn("?a=&#x27;quoted&#x27;&amp;b=2", related)
        self.assertEqual("", detail)
        stored = StoredAINewsAnalysis(AINewsAnalysis(
            "<summary>", "<outlook>", 87, "<reason>", ("<positive>",), ("<negative>",), "<category>",
        ), "<provider>", "<model>", datetime(2026, 10, 18, tzinfo=UTC))
        detail = ai_detail_html(first, stored)
        for field in ("summary", "outlook", "reason", "positive", "negative", "category", "provider", "model"):
            self.assertIn(f"&lt;{field}&gt;", detail)
            self.assertNotIn(f"<{field}>", detail)

    def test_nas_evidence_separates_rule_scores_from_ai_and_escapes_body(self) -> None:
        evidence = StoredNewsEvidence(
            identity="article-1", article_revision_id="article-r1", body_revision_id="body-r1",
            body_status="fulltext", body_text="본문 <script>alert(1)</script>",
            core_sentences=("핵심 문장 <script>alert(1)</script>",),
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
        core_sentences = stored_news_core_sentences_html(evidence)
        self.assertIn("AI 없이 뽑은 핵심 문장", core_sentences)
        self.assertIn("핵심 문장 &lt;script&gt;alert(1)&lt;/script&gt;", core_sentences)
        self.assertNotIn("<script>", core_sentences)

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
        sentences = ("첫 번째 문장입니다.", "두 번째 문장입니다.", "세 번째 문장입니다.",
                     "네 번째 문장에는 &lt;태그&gt;가 있습니다.", "다섯 번째 문장입니다.")
        self.assertEqual(sorted(rendered.index(sentence) for sentence in sentences),
                         [rendered.index(sentence) for sentence in sentences])
        self.assertTrue(all(rendered.count(sentence) == 1 for sentence in sentences))


if __name__ == "__main__":
    unittest.main()
