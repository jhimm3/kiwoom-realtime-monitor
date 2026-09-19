from __future__ import annotations

import unittest

from kiwoom_monitor.application.news_rules import (
    SUPPLY_CONTRACT_RULE_VERSION,
    classify_supply_contract,
    grouped_candidate_identities,
)


def article(title: str, description: str = "", **extra):
    return {
        "stock_code": "005930", "stock_name": "테스트기업", "identity": extra.pop("identity", "a"),
        "title": title, "description": description, "link": extra.pop("link", "https://news/a"),
        "original_link": extra.pop("original_link", "https://origin/a"),
        "published_at": extra.pop("published_at", "2026-09-12T01:00:00+00:00"),
        "relevant": 1, "category": "기업 활동", "outlook": "긍정", "reason": "",
        "relevance_score": 90, "outlook_score": 80, **extra,
    }


class SupplyContractRuleTests(unittest.TestCase):
    def test_confirmed_reaction_keeps_fact_and_safe_won_amount(self) -> None:
        result = classify_supply_contract(
            article("[특징주] 테스트기업, 삼성전자와 500억원 공급계약 체결에 급등"),
            "테스트기업은 삼성전자와 부품 공급계약을 체결했다.",
        )
        self.assertIsNotNone(result)
        self.assertEqual("SUPPLY_CONTRACT", result.event_type)
        self.assertEqual("CONFIRMED", result.certainty)
        self.assertEqual("REACTION", result.role)
        self.assertEqual(50_000_000_000, result.amount_won)
        self.assertIsNone(result.revenue_ratio)
        self.assertEqual("삼성전자", result.counterparty)
        self.assertEqual("POSITIVE", result.targets[0]["direction"])
        self.assertEqual(SUPPLY_CONTRACT_RULE_VERSION, result.rule_version)
        self.assertTrue(any(span.fact == "market_reaction" for span in result.evidence_spans))

    def test_mou_denial_and_termination_are_preserved(self) -> None:
        mou = classify_supply_contract(article("테스트기업, 고객사와 공급 MOU 체결"))
        denied = classify_supply_contract(article("테스트기업 공급계약설 부인"))
        terminated = classify_supply_contract(article("테스트기업, 공급계약 해지 공시"))
        self.assertEqual("POTENTIAL", mou.certainty)
        self.assertTrue(mou.ai_required)
        self.assertEqual("DENIED", denied.certainty)
        self.assertEqual("NEGATIVE", denied.targets[0]["direction"])
        self.assertEqual("TERMINATED", terminated.certainty)

    def test_foreign_and_conditional_amounts_are_not_forced_to_won(self) -> None:
        foreign = classify_supply_contract(article("테스트기업, ACME와 3억달러 공급계약 체결"))
        conditional = classify_supply_contract(article("테스트기업, 삼성전자와 최대 500억원 공급계약 체결"))
        self.assertIsNone(foreign.amount_won)
        self.assertIn("amount_not_safely_convertible", foreign.ai_reason)
        self.assertIsNone(conditional.amount_won)
        self.assertTrue(conditional.conditional_amount)

    def test_title_body_conflict_becomes_explainable_unknown(self) -> None:
        result = classify_supply_contract(
            article("테스트기업, 삼성전자와 500억원 공급계약 체결"),
            "회사 측은 해당 공급계약을 체결한 바 없다고 부인했다.",
        )
        self.assertEqual("UNKNOWN", result.certainty)
        self.assertTrue(result.title_body_conflict)
        self.assertIn("title_body_conflict", result.ai_reason)
        self.assertLessEqual(result.confidence_score, 30)

    def test_republication_and_unknown_remain_distinct(self) -> None:
        republished = classify_supply_contract(article("테스트기업 지난해 공급계약 재조명"))
        unknown = classify_supply_contract(article("테스트기업 공급계약 관련 보도"))
        self.assertEqual("REPUBLICATION", republished.novelty)
        self.assertEqual("UNKNOWN", unknown.certainty)
        self.assertTrue(unknown.ai_required)

    def test_existing_grouping_is_only_used_for_candidate_matching(self) -> None:
        current = article("테스트기업 삼성전자 500억원 공급계약 체결", identity="current")
        prior = article(
            "테스트기업, 삼성전자와 500억원 공급계약",
            identity="prior", link="https://news/prior", original_link="https://origin/prior",
            published_at="2026-09-12T00:00:00+00:00",
        )
        self.assertEqual(("prior",), grouped_candidate_identities(current, (prior,)))

    def test_stock_price_is_not_used_as_contract_amount(self) -> None:
        result = classify_supply_contract(
            article("테스트기업 53만원대로 밀려…실적 턴어라운드에도 약세", "고객사와 공급계약 체결"),
            "테스트기업은 53만원에 거래 중이다. 회사는 고객사와 공급계약을 체결했다.",
        )
        self.assertIsNotNone(result)
        self.assertIsNone(result.amount_won)
        self.assertIsNone(result.amount_text)

    def test_contract_amount_is_selected_instead_of_earlier_stock_price(self) -> None:
        result = classify_supply_contract(
            article("테스트기업 1만2470원 급등…장중 강세", "고객사와 220억원 공급계약 체결"),
            "테스트기업은 1만2470원에 거래됐다. 회사는 고객사와 220억원 공급계약을 체결했다.",
        )
        self.assertIsNotNone(result)
        self.assertEqual(22_000_000_000, result.amount_won)
        self.assertEqual("220억원", result.amount_text)

    def test_order_backlog_is_not_a_supply_contract_event(self) -> None:
        result = classify_supply_contract(
            article("테스트기업 20만원대 강보합"),
            "반도체 장비 수주잔고는 2336억원으로 집계됐다.",
        )
        self.assertIsNone(result)

    def test_price_reaction_does_not_promote_old_contract_from_body(self) -> None:
        result = classify_supply_contract(
            article("테스트기업 53만원대로 밀려…장중 약세", "차익 매물이 나왔다."),
            "테스트기업은 지난달 고객사와 500억원 공급계약을 체결했다.",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
