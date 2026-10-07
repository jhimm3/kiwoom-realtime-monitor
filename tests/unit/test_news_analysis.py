from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from urllib.error import HTTPError

from kiwoom_monitor.application.news_analysis import (
    NewsAssessment, assess_stock_news, extractive_news_summary,
)
from kiwoom_monitor.infrastructure.naver_news import (
    NaverNewsClient, NaverNewsCredentials, NewsFilterSettings, StockNewsItem, is_excluded_news,
    is_excluded_provider, news_provider, news_provider_domain, provider_name,
)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class NewsAnalysisTest(unittest.TestCase):
    def test_extractive_summary_keeps_lead_and_material_numeric_fact_in_source_order(self) -> None:
        body = (
            "삼성전자는 신사업 계획을 발표했다. 시장의 관심이 이어지고 있다. "
            "회사는 14일 5000억원 규모의 설비투자를 결정했다고 공시했다. "
            "주가는 장중 2% 상승했다. 관계자는 생산능력이 30% 증가한다고 설명했다. "
            "업계의 일반적인 전망도 긍정적이다."
        )

        summary = extractive_news_summary("삼성전자", "삼성전자 5000억원 설비투자", body, max_sentences=3)

        self.assertEqual("삼성전자는 신사업 계획을 발표했다.", summary[0])
        self.assertIn("5000억원 규모의 설비투자", summary[1])
        self.assertIn("생산능력이 30% 증가", summary[2])
        self.assertNotIn(" ".join(summary), "시장의 관심이 이어지고 있다.")

    def test_extractive_summary_returns_original_sentences_without_generation(self) -> None:
        body = "첫 문장은 원문입니다. 둘째 문장도 원문입니다. 셋째 문장입니다."
        summary = extractive_news_summary("", "", body)
        self.assertEqual(
            ("첫 문장은 원문입니다.", "둘째 문장도 원문입니다.", "셋째 문장입니다."),
            summary,
        )

    def test_extractive_summary_omits_photo_caption_and_keeps_title_price_gap(self) -> None:
        body = (
            "관계자들이 상장 기념 행사에서 박수를 치고 있다. "
            "국내 주가는 169만원이고 미국 ADR 환산 가격은 255만원이다. "
            "본주와 ADR 가격 괴리율은 50.7%까지 벌어졌다. "
            "증권사는 목표주가 245달러를 제시했다. 일반적인 회사 소개 문장이다."
        )

        summary = extractive_news_summary("SK하이닉스", "하이닉스 ADR 50% 비싸다", body)

        self.assertFalse(any("박수를 치고 있다" in sentence for sentence in summary))
        self.assertTrue(any("괴리율은 50.7%" in sentence for sentence in summary))

    def test_extractive_summary_never_selects_portal_footer(self) -> None:
        body = (
            "삼성전자는 15일 5000억원 규모의 신규 설비투자를 결정했다고 공시했다. "
            "회사는 생산능력이 20% 늘어날 것으로 예상했다. "
            "Copyright ⓒ 예시신문. 좋아요 0 나빠요 0 기자의 다른 기사"
        )
        summary = extractive_news_summary("삼성전자", "삼성전자 5000억원 설비투자", body)
        self.assertTrue(summary)
        self.assertTrue(all("Copyright" not in sentence and "좋아요" not in sentence for sentence in summary))

    def test_extractive_summary_abstains_from_unbroken_page_listing(self) -> None:
        body = "신문 지면의 기사 제목 목록 " + ("현대차 삼성전자 경제 일정 목록 " * 80)
        self.assertEqual((), extractive_news_summary("현대차", "신문 지면 미리보기", body))

    def test_ad_filter_uses_custom_words_and_can_be_disabled(self) -> None:
        item = StockNewsItem("삼성전자 할인 이벤트 광고", "쿠폰 증정", "", "", None, assess_stock_news("삼성전자", "삼성전자", ""))
        self.assertTrue(is_excluded_news(item, NewsFilterSettings(True, ("광고",))))
        self.assertFalse(is_excluded_news(item, NewsFilterSettings(False, ("광고",))))

    def test_provider_filter_accepts_press_name_or_domain(self) -> None:
        item = StockNewsItem("삼성전자 실적", "영업이익 증가", "", "https://www.yna.co.kr/view/1", None, assess_stock_news("삼성전자", "삼성전자 실적", "영업이익 증가"))
        self.assertEqual("연합뉴스", news_provider(item))
        self.assertTrue(is_excluded_news(item, NewsFilterSettings(True, (), ("연합뉴스",))))
        self.assertTrue(is_excluded_news(item, NewsFilterSettings(True, (), ("yna.co.kr",))))
        self.assertFalse(is_excluded_news(item, NewsFilterSettings(True, (), ("연합뉴스",), False)))

    def test_provider_identity_normalizes_known_subdomain_and_unknown_domain(self) -> None:
        item = StockNewsItem(
            "제목", "요약", "", "https://news.einfomax.co.kr/news/articleView.html?idxno=1",
            None, NewsAssessment(True, "기타", "중립", "", 1, 0),
        )
        self.assertEqual("news.einfomax.co.kr", news_provider_domain(item))
        self.assertEqual("연합인포맥스", news_provider(item))
        self.assertEqual("비즈니스포스트", provider_name("admin.businesspost.co.kr"))
        self.assertTrue(is_excluded_provider(
            "news.einfomax.co.kr", "연합인포맥스", ("연합인포맥스",),
        ))
        self.assertTrue(is_excluded_provider("unknown.example", "unknown.example", ("example",)))

    def test_supply_contract_is_relevant_and_positive_candidate(self) -> None:
        result = assess_stock_news("삼성전자", "삼성전자, 대규모 공급계약 수주", "매출 증가 기대")
        self.assertTrue(result.relevant)
        self.assertEqual("수주·계약", result.category)
        self.assertTrue(result.outlook.startswith("호재"))

    def test_rights_offering_is_negative_candidate(self) -> None:
        result = assess_stock_news("테스트기업", "테스트기업 500억원 유상증자 공시", "운영자금 조달")
        self.assertTrue(result.relevant)
        self.assertTrue(result.outlook.startswith("악재"))

    def test_product_review_without_securities_context_is_filtered(self) -> None:
        result = assess_stock_news("현대차", "현대차 신형 SUV 시승기", "주말 가족 여행에 어울리는 차량")
        self.assertFalse(result.relevant)

    def test_hot_potato_idiom_is_not_treated_as_capital_reduction(self) -> None:
        result = assess_stock_news("테스트기업", "테스트기업 정책의 뜨거운 감자", "주가에 미칠 영향이 관심")
        self.assertFalse(result.outlook.startswith("악재"))
        self.assertNotEqual("자본·주주환원", result.category)

    def test_past_drop_followed_by_rebound_is_filtered_as_price_reaction(self) -> None:
        result = assess_stock_news(
            "테스트기업", "테스트기업 주가 반등 흐름", "최근 급감했던 주가가 반등 흐름을 이어간다",
        )
        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_price_reaction_without_new_event_is_filtered(self) -> None:
        result = assess_stock_news(
            "삼성전자", "삼성전자 7만원선 탈환…장중 급등",
            "매수세가 집중되며 거래량이 증가했다.",
            article_body=(
                "삼성전자가 장중 7만원선을 회복했다. 지난달 공급계약과 "
                "반도체 업황 회복 기대가 배경으로 거론된다."
            ),
        )
        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_price_reaction_with_event_in_title_is_kept(self) -> None:
        result = assess_stock_news(
            "삼성전자", "[특징주] 삼성전자, 500억원 공급계약 체결에 급등", "장중 강세",
            article_body="삼성전자는 이날 공급계약을 체결했다고 공시했다.",
        )
        self.assertTrue(result.relevant)
        self.assertEqual("수주·계약", result.category)

    def test_price_reaction_with_fresh_event_in_body_lead_is_kept(self) -> None:
        result = assess_stock_news(
            "코나아이", "코나아이 장중 10% 강세", "주가가 상승했다.",
            article_body="코나아이는 이날 자사주 소각을 결정했다고 발표했다. 주가는 장중 10% 올랐다.",
        )
        self.assertTrue(result.relevant)

    def test_exchange_rate_drop_and_earnings_forecast_revision_is_kept(self) -> None:
        result = assess_stock_news(
            "SK하이닉스",
            "SK하이닉스 '환율' 급락에 영업익 전망 14조원 감소",
            "증권가 컨센서스와 3분기 영업이익 전망치가 하향 조정됐다.",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("실적·전망", result.category)

    def test_exchange_rate_drop_does_not_hide_separate_stock_price_drop(self) -> None:
        result = assess_stock_news(
            "삼성전자", "환율 급락에 삼성전자 주가도 장중 급락", "매도세가 이어졌다.",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_investor_sentiment_is_not_classified_as_merger_or_investment_event(self) -> None:
        result = assess_stock_news(
            "삼성전자", "삼성전자 주가 약세", "투자심리가 위축되며 장중 하락했다.",
        )

        self.assertNotEqual("투자·인수합병", result.category)

    def test_market_open_drop_is_excluded_as_market_reaction(self) -> None:
        result = assess_stock_news(
            "삼성전자", "코스피 3%대 하락 출발...외국인 매도세",
            "삼성전자와 SK하이닉스도 약세를 보였다.",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_direct_percentage_drop_is_excluded_as_stock_reaction(self) -> None:
        result = assess_stock_news(
            "현대차", "현대차, 자율주행 상용화 3년 후...3%대 하락[핫종목]",
            "현대차 주가가 장중 하락했다.",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_market_summary_remains_excluded_when_it_mentions_exports(self) -> None:
        result = assess_stock_news(
            "삼성전기", "[오늘증시] 코스피 상승 마감...반도체 수출 증가",
            "삼성전기도 강세를 보였다.",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_material_product_and_export_news_is_kept(self) -> None:
        product = assess_stock_news(
            "한화오션", "한화오션, 자체 개발 부유식 데이터센터 첫 공개", "신기술을 공개했다.",
        )
        exports = assess_stock_news(
            "현대차", "현대차 하이브리드차 대미 수출 70% 급증", "수출 판매가 늘었다.",
        )

        self.assertTrue(product.relevant)
        self.assertTrue(exports.relevant)

    def test_full_company_name_in_clean_body_lead_supports_alias_title(self) -> None:
        result = assess_stock_news(
            "에스투더블유", "[특징주] S2W, 정부 AI 모델 개발 참여", "주가 관련 소식",
            article_body="에스투더블유는 이날 정부 AI 모델 개발 사업에 참여한다고 밝혔다.",
        )

        self.assertTrue(result.relevant)

    def test_unrelated_title_is_not_kept_from_summary_mention_only(self) -> None:
        result = assess_stock_news(
            "삼성전자", "2027학년도 수시 모집 전략", "삼성전자 취업 증가 사례도 소개했다.",
        )

        self.assertFalse(result.relevant)

    def test_market_roundup_tag_is_excluded_without_explicit_move_word(self) -> None:
        result = assess_stock_news(
            "삼성전자", "긴축 경계감 속 코스피 6700선…개인 2조 저가 매수 [장중시황]",
            "삼성전자도 거래됐다.",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_non_price_percentage_arrows_are_not_price_reactions(self) -> None:
        sales = assess_stock_news(
            "현대차", "현대차 베트남 판매 20%↓", "현대차 판매량이 감소했다.",
        )
        reuse = assess_stock_news(
            "SK하이닉스", "SK하이닉스 물 재이용량 4년 새 54%↑", "사용량을 줄였다.",
        )

        self.assertNotEqual("시세 반영·시장 요약", sales.category)
        self.assertNotEqual("시세 반영·시장 요약", reuse.category)

    def test_hot_stock_percentage_move_is_excluded(self) -> None:
        result = assess_stock_news(
            "현대차", "현대차 자율주행 상용화 3년 후…3%대 하락[핫종목]", "주가 약세",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_direct_lawsuit_is_kept_as_material_regulatory_event(self) -> None:
        result = assess_stock_news(
            "삼성중공업", "삼성중공업, 2499억원 손해배상 소송 제기", "법원에 소장이 접수됐다.",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("공시·규제", result.category)

    def test_sports_photo_is_excluded_as_low_value_company_news(self) -> None:
        result = assess_stock_news(
            "KB금융", "[포토] KB금융 스타챔피언십 골프 선수 티샷", "대회 첫날 경기 사진",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("비투자성 기업 소식", result.category)

    def test_amount_supply_event_is_kept_despite_price_move(self) -> None:
        result = assess_stock_news(
            "두산퓨얼셀", "두산퓨얼셀, 5014억원 미국 데이터센터 연료전지 공급에 급등", "장중 강세",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("수주·계약", result.category)

    def test_earnings_outlook_arrow_is_not_stock_price_reaction(self) -> None:
        result = assess_stock_news(
            "삼성전자", "삼성전자 영업익 전망 21조↓", "환율 하락으로 전망치가 낮아졌다.",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("실적·전망", result.category)

    def test_direct_company_percentage_move_without_event_is_excluded(self) -> None:
        result = assess_stock_news(
            "삼성전자", "삼성전자 3.7% 내려", "외국인 매도로 약세를 보였다.",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_sales_share_percentage_move_is_not_stock_price_reaction(self) -> None:
        result = assess_stock_news(
            "현대차", "현대차 미국 판매 점유율 3.7% 하락", "판매량과 점유율이 감소했다.",
        )

        self.assertNotEqual("시세 반영·시장 요약", result.category)

    def test_market_close_roundup_is_excluded_for_every_mapped_stock(self) -> None:
        result = assess_stock_news(
            "대우건설",
            "[주식마감] '2분기 흑자전환' 라온피플 상한가…대우건설 약세",
            "여러 종목의 등락을 정리했다.",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_exchange_list_roundup_is_excluded(self) -> None:
        result = assess_stock_news(
            "심텍", "[코스피·코스닥, SK이노베이션 심텍 우원개발 등]", "종목별 등락을 정리했다.",
        )

        self.assertFalse(result.relevant)
        self.assertEqual("시세 반영·시장 요약", result.category)

    def test_investor_flow_and_turnover_rankings_are_market_summaries(self) -> None:
        values = (
            ("SK하이닉스", "[거래소 외국인] SK하이닉스에 거센 매도 폭탄…삼성전자까지 던졌다"),
            ("HPSP", "[주간 코스닥 기관] HPSP 순매수 1위…반도체주 집중"),
            ("SK하이닉스", "[공매도 브리핑] SK하이닉스 7743억 1위"),
            ("SK하이닉스", "SK하이닉스·삼성전자 거래대금 11.3조…상위 50종목의 58.7%"),
            ("SK하이닉스", "한 달 새 증시 시총 212조 증발…SK하이닉스 부진 직격탄"),
        )

        for stock_name, title in values:
            with self.subTest(title=title):
                result = assess_stock_news(
                    stock_name, title, "한국거래소 집계와 종목별 거래 내역을 정리했다.",
                )
                self.assertFalse(result.relevant)
                self.assertEqual("시세 반영·시장 요약", result.category)

    def test_rights_offering_event_is_kept_despite_market_cap_reaction(self) -> None:
        result = assess_stock_news(
            "삼성바이오로직스",
            "삼성바이오로직스, 3조 유상증자에 6.8% 급락…시총 5조 증발",
            "회사는 유상증자를 결정했다고 공시했다.",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("자본·주주환원", result.category)
        self.assertTrue(result.outlook.startswith("악재"))

    def test_noninvestment_employee_transport_and_cultural_space_are_filtered(self) -> None:
        values = (
            ("'300억' SK하이닉스 셔틀버스를 잡아라", "지역 운송업체 선정 문제를 다뤘다."),
            ("용인시·SK하이닉스 맞손…복합인문문화공간 조성", "지역사회 협력 사업이다."),
        )

        for title, description in values:
            with self.subTest(title=title):
                result = assess_stock_news("SK하이닉스", title, description)
                self.assertFalse(result.relevant)
                self.assertEqual("비투자성 기업 소식", result.category)

    def test_body_lead_contributes_to_direction_only_after_relevance_is_confirmed(self) -> None:
        positive = assess_stock_news(
            "테스트기업", "테스트기업 실적 발표", "분기 실적을 발표했다.",
            article_body="테스트기업은 이날 영업이익이 전년보다 45% 증가했다고 발표했다.",
        )
        negative = assess_stock_news(
            "테스트기업", "테스트기업 실적 발표", "분기 실적을 발표했다.",
            article_body="테스트기업은 이날 영업손실이 확대돼 적자가 지속됐다고 발표했다.",
        )

        self.assertTrue(positive.relevant)
        self.assertTrue(positive.outlook.startswith("호재"))
        self.assertTrue(negative.relevant)
        self.assertTrue(negative.outlook.startswith("악재"))

    def test_body_background_does_not_flip_clear_summary_direction(self) -> None:
        result = assess_stock_news(
            "테스트기업", "테스트기업 대규모 공급계약 체결", "신규 수주를 공시했다.",
            article_body="과거에는 영업손실이 확대됐으나 이번 계약으로 매출 증가가 예상된다.",
        )

        self.assertTrue(result.relevant)
        self.assertTrue(result.outlook.startswith("호재"))

    def test_body_direction_ignores_other_company_sentence(self) -> None:
        result = assess_stock_news(
            "우리기술", "우리기술 공시, 공매도 과열종목 지정", "하루 동안 공매도가 금지된다.",
            article_body=(
                "우리기술은 공매도 과열종목으로 지정됐다. "
                "유디엠텍은 별도로 매매거래정지가 예고됐다."
            ),
        )

        self.assertTrue(result.relevant)
        self.assertEqual("판단 보류", result.outlook)

    def test_body_direction_ignores_weak_generic_change_words(self) -> None:
        result = assess_stock_news(
            "테스트기업", "테스트기업 신규 사업 참여", "수자원 재이용 정책을 설명했다.",
            article_body="테스트기업은 재이용량을 크게 증가시키고 물 사용량은 감소시켰다.",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("판단 보류", result.outlook)

    def test_confirmed_company_event_after_old_direction_window_is_used(self) -> None:
        result = assess_stock_news(
            "삼성전기", "삼성전기 실적 발표", "AI 서버 부품 사업을 설명했다.",
            article_body=("산업 배경 설명입니다. " * 45)
            + "삼성전기는 글로벌 고객사와 1조원 규모 공급계약을 맺었다고 1일 공시했다.",
        )

        self.assertTrue(result.relevant)
        self.assertTrue(result.outlook.startswith("호재"))

    def test_late_past_or_unconfirmed_contract_does_not_set_direction(self) -> None:
        prefix = "산업 배경 설명입니다. " * 45
        past = assess_stock_news(
            "우리기술", "우리기술 공시 원전 사업 점검", "원전 산업 현황을 설명했다.",
            article_body=prefix + "우리기술은 지난 5월 공급계약을 체결했다.",
        )
        unconfirmed = assess_stock_news(
            "우리기술", "우리기술 공시 원전 사업 점검", "원전 산업 현황을 설명했다.",
            article_body=prefix + "우리기술의 직접 수주가 확정된 사안은 아니지만 시장이 주목한다.",
        )

        self.assertEqual("판단 보류", past.outlook)
        self.assertEqual("판단 보류", unconfirmed.outlook)

    def test_late_other_company_event_does_not_set_target_direction(self) -> None:
        result = assess_stock_news(
            "삼성전자", "삼성전자 공시 반도체 산업 전망", "반도체 업황을 설명했다.",
            article_body=("산업 배경 설명입니다. " * 45)
            + "경쟁사는 대규모 공급계약을 체결했다. 삼성전자는 비교 대상으로 언급됐다.",
        )

        self.assertEqual("판단 보류", result.outlook)

    def test_price_reaction_title_does_not_take_late_analyst_opinion_as_new_direction(self) -> None:
        result = assess_stock_news(
            "로보티즈", "로보티즈 26% 급등…목표가 주목", "장중 주가 움직임을 설명했다.",
            article_body=("장중 거래 배경입니다. " * 45)
            + "증권사는 로보티즈에 대해 투자의견 매수를 유지했다.",
        )

        self.assertEqual("판단 보류", result.outlook)

    def test_profit_growth_is_not_negative_because_other_activity_expands(self) -> None:
        result = assess_stock_news(
            "테스트기업", "테스트기업 실적 발표", "영업이익이 증가했고 설비투자도 확대했다.",
        )

        self.assertTrue(result.relevant)
        self.assertTrue(result.outlook.startswith("호재"))

    def test_material_positive_events_do_not_remain_on_hold(self) -> None:
        values = (
            ("현대차, 자사주 8000억 전량 소각", "주주환원 계획을 발표했다."),
            ("현대건설, 2.6조 재건축 시공사 우선협상대상자 선정", "관련 내용을 공시했다."),
            ("한미약품, 3.2조 기술이전 계약", "재무 여력이 크게 개선될 전망이다."),
            ("대우건설, 일반분양 1032가구 전량 계약 마감", "분양 계약을 모두 마쳤다."),
        )

        for title, description in values:
            with self.subTest(title=title):
                result = assess_stock_news(title.split(",", 1)[0], title, description)
                self.assertTrue(result.relevant)
                self.assertTrue(result.outlook.startswith("호재"))

    def test_explicit_analyst_bullish_phrases_do_not_remain_on_hold(self) -> None:
        result = assess_stock_news(
            "테스트기업", "테스트기업 목표가 상향", "투자의견 매수를 유지하고 수혜가 기대된다고 평가했다.",
        )

        self.assertTrue(result.relevant)
        self.assertTrue(result.outlook.startswith("호재"))

    def test_resolved_labor_vote_does_not_remain_negative_from_old_rejection(self) -> None:
        result = assess_stock_news(
            "SK하이닉스", "SK하이닉스 노사 성과급 자사주 지급 재합의",
            "기존 합의안이 부결된 뒤 현금 비중을 높여 새 합의안을 마련했다.",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("판단 보류", result.outlook)

    def test_first_loss_is_negative_but_future_turnaround_can_balance_it(self) -> None:
        loss = assess_stock_news(
            "테스트기업", "테스트기업 주력 사업 사상 첫 적자", "수익성이 악화됐다.",
        )
        mixed = assess_stock_news(
            "테스트기업", "테스트기업 만년 적자 자회사 다시 품어", "2년 내 흑자전환을 목표로 한다.",
        )

        self.assertTrue(loss.outlook.startswith("악재"))
        self.assertEqual("판단 보류", mixed.outlook)

    def test_contract_delay_balances_old_preferred_bidder_fact(self) -> None:
        result = assess_stock_news(
            "한화오션", "한화오션 호위함 계약 연기 촉구",
            "우선협상대상자로 선정됐지만 현지 의회가 계약 연기를 촉구했다.",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("판단 보류", result.outlook)

    def test_hypothetical_capacity_growth_does_not_imply_positive_direction(self) -> None:
        result = assess_stock_news(
            "SK하이닉스", "SK하이닉스 물 재이용 확대", "생산능력이 확대될수록 용수가 더 필요하다.",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("판단 보류", result.outlook)

    def test_genuinely_ambiguous_company_event_remains_on_hold(self) -> None:
        result = assess_stock_news(
            "SK하이닉스", "SK하이닉스, 한전 전기료 선납 제안 거절",
            "전력망 투자 재원 마련 방안을 두고 협의를 이어간다.",
        )

        self.assertTrue(result.relevant)
        self.assertEqual("판단 보류", result.outlook)

    def test_other_company_event_is_not_assigned_from_late_body_mention(self) -> None:
        result = assess_stock_news(
            "JW신약", "온코닉테라퓨틱스 미국 특허 등록", "신약 특허를 취득했다.",
            article_body=(
                "온코닉테라퓨틱스가 미국 특허를 등록했다. 관련 업종을 설명하는 후반부에서 "
                "JW신약의 기존 제품도 함께 언급했다."
            ),
        )

        self.assertFalse(result.relevant)

    def test_alias_title_with_company_in_body_opening_is_still_kept(self) -> None:
        result = assess_stock_news(
            "에스투더블유", "S2W, 정부 AI 개발사업 참여", "정부 사업에 참여한다.",
            article_body="에스투더블유는 이날 정부 AI 개발사업에 참여한다고 발표했다.",
        )

        self.assertTrue(result.relevant)

    @patch("kiwoom_monitor.infrastructure.naver_news.urlopen")
    def test_naver_client_cleans_and_assesses_results(self, mocked_urlopen: object) -> None:
        mocked_urlopen.return_value = _Response({
            "items": [{
                "title": "<b>삼성전자</b>, 공급계약 수주",
                "description": "영업이익 증가 기대",
                "link": "https://n.news.naver.com/article/1",
                "originallink": "https://example.com/article/1",
                "pubDate": "Wed, 26 Aug 2026 09:10:00 +0900",
            }]
        })
        client = NaverNewsClient(NaverNewsCredentials("id", "secret"))
        items = client.search("삼성전자")
        self.assertEqual(1, len(items))
        request = mocked_urlopen.call_args.args[0]
        self.assertTrue(request.full_url.startswith("https://naverapihub.apigw.ntruss.com/search/v1/news?"))
        headers = {key.casefold(): value for key, value in request.header_items()}
        self.assertEqual("id", headers["x-ncp-apigw-api-key-id"])
        self.assertEqual("삼성전자, 공급계약 수주", items[0].title)
        self.assertTrue(items[0].assessment.relevant)
        self.assertTrue(items[0].assessment.outlook.startswith("호재"))

    @patch("kiwoom_monitor.infrastructure.naver_news.urlopen")
    def test_naver_client_pages_until_cutoff_and_skips_missing_dates(self, mocked_urlopen: object) -> None:
        first_page = [{
            "title": f"삼성전자 뉴스 {index}", "description": "실적 발표",
            "link": f"https://n.news.naver.com/article/{index}",
            "pubDate": "Wed, 26 Aug 2026 09:10:00 +0900",
        } for index in range(100)]
        second_page = [
            {"title": "날짜 없음", "link": "https://example.com/no-date", "pubDate": ""},
            {"title": "조회 기준 이전", "link": "https://example.com/old", "pubDate": "Mon, 24 Aug 2026 09:10:00 +0900"},
        ]
        mocked_urlopen.side_effect = [_Response({"items": first_page}), _Response({"items": second_page})]

        items = NaverNewsClient(NaverNewsCredentials("id", "secret")).search(
            "삼성전자", since=datetime(2026, 8, 25, tzinfo=UTC),
        )

        self.assertEqual(100, len(items))
        self.assertEqual(2, mocked_urlopen.call_count)
        self.assertIn("start=101", mocked_urlopen.call_args.args[0].full_url)

    @patch("kiwoom_monitor.infrastructure.naver_news.urlopen")
    def test_naver_page_exposes_request_metadata_and_claims_before_call(self, mocked_urlopen: object) -> None:
        mocked_urlopen.return_value = _Response({"total": 321, "start": 101, "items": [{
            "title": "삼성전자 뉴스", "description": "실적 발표", "link": "https://n/1",
            "pubDate": "Wed, 26 Aug 2026 09:10:00 +0900",
        }]})
        claims = []
        page = NaverNewsClient(NaverNewsCredentials("id", "secret")).search_page(
            "증권", start=101, display=100, request_claim=lambda: claims.append("claimed") is None,
        )
        self.assertEqual((321, 101, 1), (page.total, page.start, page.display))
        self.assertEqual(["claimed"], claims)

    @patch("kiwoom_monitor.infrastructure.naver_news.urlopen")
    def test_legacy_fallback_claims_both_actual_requests(self, mocked_urlopen: object) -> None:
        mocked_urlopen.side_effect = [
            HTTPError("https://hub", 401, "unauthorized", {}, None),
            _Response({"total": 0, "start": 1, "items": []}),
        ]
        claims = []
        NaverNewsClient(NaverNewsCredentials("id", "secret")).search_page(
            "증권", request_claim=lambda: claims.append("claimed") is None,
        )
        self.assertEqual(["claimed", "claimed"], claims)


if __name__ == "__main__":
    unittest.main()
