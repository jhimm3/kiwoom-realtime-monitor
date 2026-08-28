from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import (
    NaverNewsClient, NaverNewsCredentials, NewsFilterSettings, StockNewsItem, is_excluded_news, news_provider,
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

    def test_past_drop_followed_by_rebound_is_not_negative(self) -> None:
        result = assess_stock_news(
            "테스트기업", "테스트기업 주가 반등 흐름", "최근 급감했던 주가가 반등 흐름을 이어간다",
        )
        self.assertTrue(result.relevant)
        self.assertTrue(result.outlook.startswith("호재"))
        self.assertIn("반등", result.reason)

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


if __name__ == "__main__":
    unittest.main()
