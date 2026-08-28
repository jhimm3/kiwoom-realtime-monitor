from kiwoom_monitor.infrastructure.news_ai import _parse


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
