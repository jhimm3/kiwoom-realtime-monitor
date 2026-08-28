from kiwoom_monitor.infrastructure.article_text import _ArticleParser


def test_article_parser_prefers_known_article_container() -> None:
    parser = _ArticleParser()
    parser.feed('<html><p>외부</p><div id="dic_area"><p>본문 첫 문장</p><p>본문 둘째 문장</p></div></html>')
    assert "본문 첫 문장" in parser.article
    assert "외부" not in parser.article
