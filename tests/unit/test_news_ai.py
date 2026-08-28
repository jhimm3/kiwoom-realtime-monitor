from unittest.mock import patch

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings
from kiwoom_monitor.infrastructure.news_ai import AIRequestUsage, _parse, analyze_articles


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
