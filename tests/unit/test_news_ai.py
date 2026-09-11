from unittest.mock import patch

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings
from kiwoom_monitor.infrastructure.news_ai import (
    ANALYSIS_PROMPT_VERSION, AIRequestUsage, NewsAIProviderError, _parse, _request,
    analysis_body_hash, analyze_articles,
)


def test_parse_ai_result_accepts_json_wrapped_in_text() -> None:
    result = _parse('result: {"summary":"요약","category":"수주·계약","outlook":"혼재","confidence":72,"reason":"양쪽 근거","positive_evidence":["수주"],"negative_evidence":["비용"]}')
    assert result.outlook == "혼재"
    assert result.confidence == 72
    assert result.positive_evidence == ("수주",)
    assert result.category == "수주·계약"


def test_parse_ai_result_clamps_confidence_and_unknown_outlook() -> None:
    result = _parse('{"summary":"","outlook":"대박","confidence":120,"reason":""}')
    assert result.outlook == "판단 자료 부족"
    assert result.confidence == 100


def test_batch_analysis_uses_one_provider_request_for_multiple_events() -> None:
    response = """[
      {"id":1,"summary":"첫째","category":"수주·계약","outlook":"긍정","confidence":80,"reason":"계약","positive_evidence":[],"negative_evidence":[]},
      {"id":2,"summary":"둘째","category":"공시·규제","outlook":"혼재","confidence":70,"reason":"변경","positive_evidence":[],"negative_evidence":[]}
    ]"""
    with patch("kiwoom_monitor.infrastructure.news_ai._gemini", return_value=(response, AIRequestUsage(1200, 300, 1500))) as request:
        results, usage = analyze_articles(
            NewsAISettings("gemini", "key", "model"), "테스트기업",
            (("첫 기사", "첫 본문"), ("둘째 기사", "둘째 본문")),
        )

    assert request.call_count == 1
    assert [result.summary for result in results] == ["첫째", "둘째"]
    assert usage == AIRequestUsage(1200, 300, 1500)


def test_ai_prompt_requires_the_selected_stock_perspective() -> None:
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


def test_retryable_provider_error_preserves_upstream_status() -> None:
    from urllib.error import HTTPError

    error = HTTPError("https://ai.test", 429, "limited", {}, None)
    with patch("kiwoom_monitor.infrastructure.news_ai.urlopen", side_effect=error), \
            patch("kiwoom_monitor.infrastructure.news_ai.time.sleep"):
        try:
            _request("https://ai.test", {}, {})
        except NewsAIProviderError as raised:
            assert raised.status_code == 429
            assert "호출 한도" in str(raised)
        else:
            raise AssertionError("NewsAIProviderError가 발생해야 합니다.")
