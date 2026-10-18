import unittest

from unittest.mock import patch

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings
from kiwoom_monitor.infrastructure.news_ai import (
    ANALYSIS_PROMPT_VERSION, AIRequestUsage, NewsAIProviderError, _parse, _request,
    analysis_body_hash, analyze_articles,
)


class MarketResearchSourceTests(unittest.TestCase):
    def test_api_research_without_search_sources_preserves_completed_text_and_usage(self):
        from kiwoom_monitor.infrastructure.news_ai import research_market
        final = {'status':'completed','output':[{'type':'message','status':'completed','role':'assistant',
            'content':[{'type':'output_text','text':'research output'}]}],
            'usage':{'input_tokens':2,'output_tokens':3,'total_tokens':5}}
        with patch('kiwoom_monitor.infrastructure.news_ai._request',return_value=final) as request:
            reply = research_market(NewsAISettings('openai','fake-key','model'),'prompt')
        self.assertEqual('research output',reply.text)
        self.assertEqual(5,reply.usage['total_tokens'])
        self.assertEqual((),reply.sources)
        self.assertEqual(1,request.call_count)

    def test_api_research_retains_same_open_page_evidence_as_subscription(self):
        from kiwoom_monitor.infrastructure.news_ai import research_market
        url = 'https://example.test/opened'
        final = {'status': 'completed', 'output': [
            {'type': 'web_search_call', 'status': 'completed',
             'action': {'type': 'open_page', 'url': url}},
            {'type': 'message', 'status': 'completed', 'role': 'assistant',
             'content': [{'type': 'output_text', 'text': 'research output'}]},
        ], 'usage': {'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5}}
        with patch('kiwoom_monitor.infrastructure.news_ai._request', return_value=final) as request:
            reply = research_market(NewsAISettings('openai', 'fake-key', 'model'), 'prompt')
        self.assertEqual(({'url': url, 'title': ''},), reply.sources)
        self.assertEqual(({'type': 'open_page', 'urls': [url]},), reply.web_actions)
        self.assertEqual(1, request.call_count)


class TestNewsAi(unittest.TestCase):
    def test_parse_ai_result_accepts_json_wrapped_in_text(self) -> None:
        result = _parse('result: {"summary":"요약","category":"수주·계약","outlook":"혼재","confidence":72,"reason":"양쪽 근거","positive_evidence":["수주"],"negative_evidence":["비용"],"theme_candidates":[{"name":"호남클러스터","confidence":81,"evidence":"복수 기업 참여"}]}')
        assert result.outlook == "혼재"
        assert result.confidence == 72
        assert result.positive_evidence == ("수주",)
        assert result.category == "수주·계약"
        assert result.theme_candidates[0].name == "호남클러스터"
        assert result.theme_candidates[0].evidence == "복수 기업 참여"


    def test_parse_ai_result_clamps_confidence_and_unknown_outlook(self) -> None:
        result = _parse('{"summary":"","outlook":"대박","confidence":120,"reason":""}')
        assert result.outlook == "판단 자료 부족"
        assert result.confidence == 100


    def test_batch_analysis_uses_one_provider_request_for_multiple_events(self) -> None:
        response = """[
          {"id":1,"summary":"첫째","category":"수주·계약","outlook":"긍정","confidence":80,"reason":"계약","positive_evidence":[],"negative_evidence":[]},
          {"id":2,"summary":"둘째","category":"공시·규제","outlook":"혼재","confidence":70,"reason":"변경","positive_evidence":[],"negative_evidence":[]}
        ]"""
        import json
        response = json.dumps(list(reversed(json.loads(response))), ensure_ascii=False)
        with patch("kiwoom_monitor.infrastructure.news_ai._gemini", return_value=(response, AIRequestUsage(1200, 300, 1500))) as request:
            results, usage = analyze_articles(
                NewsAISettings("gemini", "key", "model"), "테스트기업",
                (("첫 기사", "첫 본문"), ("둘째 기사", "둘째 본문")),
            )

        assert request.call_count == 1
        assert [result.summary for result in results] == ["첫째", "둘째"]
        assert usage == AIRequestUsage(1200, 300, 1500)


        for ids in ((1, 1), (1, 3), (0, 2)):
            malformed = json.loads(response)
            for row, identifier in zip(malformed, ids):
                row["id"] = identifier
            with self.subTest(ids=ids), patch("kiwoom_monitor.infrastructure.news_ai._gemini", return_value=(json.dumps(malformed), AIRequestUsage())):
                with self.assertRaises(ValueError):
                    analyze_articles(NewsAISettings("gemini", "key", "model"), "테스트기업", (("첫 기사", "첫 본문"), ("둘째 기사", "둘째 본문")))

    def test_ai_prompt_requires_the_selected_stock_perspective(self) -> None:
        response = '[{"id":1,"summary":"직접 관계 없음","category":"기타 증권뉴스","outlook":"판단 자료 부족","confidence":90,"reason":"단순 나열","positive_evidence":[],"negative_evidence":[]}]'
        with patch("kiwoom_monitor.infrastructure.news_ai._gemini", return_value=(response, AIRequestUsage())) as request:
            analyze_articles(
                NewsAISettings("gemini", "key", "model"), "삼성전자",
                (("시장 기사", "여러 종목을 단순 나열했다."),),
            )

        prompt = request.call_args.args[2]
        assert "반드시 위 종목 삼성전자의" in prompt
        assert "단순 나열" in prompt
        assert analysis_body_hash("삼성전자", "본문").startswith(f"{ANALYSIS_PROMPT_VERSION}:")


    def test_retryable_provider_error_preserves_upstream_status(self) -> None:
        from urllib.error import HTTPError

        error = HTTPError("https://ai.test", 429, "limited", {}, None)
        with patch("kiwoom_monitor.infrastructure.news_ai.urlopen", side_effect=error) as request, \
                patch("kiwoom_monitor.infrastructure.news_ai.time.sleep") as sleep:
            try:
                _request("https://ai.test", {}, {})
            except NewsAIProviderError as raised:
                assert raised.status_code == 429
                assert "호출 한도" in str(raised)
            else:
                raise AssertionError("NewsAIProviderError가 발생해야 합니다.")

        assert request.call_count == 3
        assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0]
        for retry_after, delay in (("0", 1.0), ("99", 5.0)):
            error = HTTPError("https://ai.test", 429, "limited", {"Retry-After": retry_after}, None)
            with patch("kiwoom_monitor.infrastructure.news_ai.urlopen", side_effect=error) as request, patch("kiwoom_monitor.infrastructure.news_ai.time.sleep") as sleep:
                with self.assertRaises(NewsAIProviderError):
                    _request("https://ai.test", {}, {})
                assert request.call_count == 3
                assert [call.args[0] for call in sleep.call_args_list] == [delay, delay]
        error = HTTPError("https://ai.test", 401, "unauthorized", {}, None)
        with patch("kiwoom_monitor.infrastructure.news_ai.urlopen", side_effect=error) as request, patch("kiwoom_monitor.infrastructure.news_ai.time.sleep") as sleep:
            with self.assertRaises(HTTPError):
                _request("https://ai.test", {}, {})
            assert request.call_count == 1
            sleep.assert_not_called()
