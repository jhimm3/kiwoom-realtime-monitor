from kiwoom_monitor.infrastructure.article_text import (
    _ArticleParser,
    clean_article_text,
    clean_article_text_with_details,
)


def test_article_parser_prefers_known_article_container() -> None:
    parser = _ArticleParser()
    parser.feed('<html><p>외부</p><div id="dic_area"><p>본문 첫 문장</p><p>본문 둘째 문장</p></div></html>')
    assert "본문 첫 문장" in parser.article
    assert "외부" not in parser.article


def test_article_parser_closes_target_after_void_elements() -> None:
    parser = _ArticleParser()
    parser.feed(
        '<div id="dic_area"><p>본문 첫 문장<br>본문 둘째 문장<img src="x"></p></div>'
        '<p>추천 기사와 페이지 푸터</p>'
    )
    assert "본문 첫 문장" in parser.article
    assert "본문 둘째 문장" in parser.article
    assert "추천 기사와 페이지 푸터" not in parser.article


def test_clean_article_text_removes_portal_tail_after_copyright() -> None:
    text = (
        "기사 본문 " * 30
        + "강중모 기자 임상혁 기자 Copyright ⓒ 파이낸셜뉴스. All rights reserved. "
        + "기자 프로필 많이 본 뉴스 함께 볼만한 뉴스"
    )
    cleaned = clean_article_text(text)
    assert "기사 본문" in cleaned
    assert "강중모 기자" in cleaned
    assert "Copyright" not in cleaned
    assert "많이 본 뉴스" not in cleaned
    details = clean_article_text_with_details(text)
    assert details[1] == "Copyright ⓒ"
    assert details[2] == text.index("Copyright ⓒ")


def test_clean_article_text_does_not_cut_short_lead_with_marker_word() -> None:
    text = "Copyright © 제도의 의미를 설명한 기사입니다. " + ("충분한 본문 " * 30)
    assert clean_article_text(text) == " ".join(text.split())


def test_clean_article_text_removes_ranking_footer_without_copyright() -> None:
    text = ("실제 기사 문장입니다. " * 20) + "많이 본 뉴스 1위 전혀 다른 기사 2위 또 다른 기사"
    cleaned, marker, _cut_at = clean_article_text_with_details(text)
    assert marker == "많이 본 뉴스"
    assert "전혀 다른 기사" not in cleaned


def test_clean_article_text_rejects_page_section_captured_as_whole_body() -> None:
    cleaned, marker, cut_at = clean_article_text_with_details("관련 기사 전혀 다른 제목과 추천 목록")
    assert cleaned == ""
    assert marker == "관련 기사"
    assert cut_at == 0


def test_clean_article_text_preserves_related_article_bundle_header() -> None:
    text = (
        "[관련 기사 1/2: 삼성전자 실적 전망] "
        + ("삼성전자의 영업이익 전망을 분석한 실제 기사입니다. " * 20)
        + "많이 본 뉴스 전혀 다른 기사"
    )
    cleaned = clean_article_text(text)
    assert cleaned.startswith("[관련 기사 1/2:")
    assert "실제 기사입니다" in cleaned
    assert "많이 본 뉴스" not in cleaned


def test_clean_article_text_cleans_each_related_article_section() -> None:
    text = (
        "[관련 기사 1/2: 첫 기사] " + ("첫 번째 실제 본문. " * 20)
        + "Copyright ⓒ 첫 언론사. "
        + "[관련 기사 2/2: 둘째 기사] " + ("두 번째 실제 본문. " * 20)
        + "많이 본 뉴스 다른 제목"
    )
    cleaned = clean_article_text(text)
    assert "[관련 기사 1/2: 첫 기사]" in cleaned
    assert "[관련 기사 2/2: 둘째 기사]" in cleaned
    assert "첫 번째 실제 본문" in cleaned
    assert "두 번째 실제 본문" in cleaned
    assert "Copyright" not in cleaned
    assert "많이 본 뉴스" not in cleaned


def test_clean_article_text_matches_copyright_case_insensitively() -> None:
    text = ("실제 기사 본문입니다. " * 20) + "ALL RIGHTS RESERVED 추천 기사"
    cleaned = clean_article_text(text)
    assert "실제 기사 본문" in cleaned
    assert "RIGHTS RESERVED" not in cleaned


def test_clean_article_text_trims_financial_news_footer_after_reporters() -> None:
    text = (
        "외국인이 4조6000억원을 순매도해 국내 증시가 하락했다. "
        "강중모 기자 (vrdw88@fnnews.com) 임상혁 기자 (yimsh0214@fnnews.com) "
        "Copyright ⓒ 파이낸셜뉴스. 많이 본 뉴스 광고와 추천 기사"
    )
    cleaned = clean_article_text(text)
    assert cleaned.endswith("임상혁 기자 (yimsh0214@fnnews.com)")
    assert "많이 본 뉴스" not in cleaned


def test_clean_article_text_rejects_known_publisher_footer_captured_as_body() -> None:
    assert clean_article_text(
        "MTN 머니투데이방송 다음뉴스 채널구독 채널구독 고충처리인 : 콘텐츠총괄부장",
    ) == ""
    assert clean_article_text(
        "ⓒ 맛있는 뉴스토마토, 무단 전재 - 재배포 금지 이 기자의 최신글",
    ) == ""


def test_clean_article_text_rejects_publisher_legal_page_captured_as_body() -> None:
    text = (
        "삼성전자 신규 기술 공개 기자 입력 2026-09-14 08:29 1 2 3 4 5 "
        "법인명 : 주식회사 예시미디어 제호 : 예시경제 대표전화 : 02-000-0000 "
        "등록번호 : 서울 아 00000 청소년보호책임자 : 홍길동 무단 사용을 금합니다."
    )

    cleaned, marker, cut_at = clean_article_text_with_details(text)

    assert cleaned == ""
    assert marker == "publisher-footer-only"
    assert cut_at == 0


def test_clean_article_text_rejects_short_photo_caption_only() -> None:
    text = (
        "(서울=뉴스1) 김기자 = 14일 서울 거래소 전광판에 코스피 시황이 "
        "표시되고 있다. 2026.9.14/뉴스1 Copyright (C) 뉴스1."
    )
    assert clean_article_text(text) == ""


def test_clean_article_text_rejects_short_headline_before_publisher_footer() -> None:
    text = "3508억원 규모 장기계약 체결 [ⓒ 서울경제TV(www.sentv.co.kr), 무단전재 금지"
    assert clean_article_text(text) == ""


def test_clean_article_text_rejects_repeated_product_image_caption() -> None:
    text = "제품 투시도.(사진=제공) 제품 투시도.(사진=제공)"
    assert clean_article_text(text) == ""


def test_clean_article_text_removes_remaining_portal_reaction_tail() -> None:
    text = ("실적과 신규 수주를 설명하는 충분한 기사 본문입니다. " * 10) + (
        "좋아요 0 나빠요 0 이 기자의 최신글 기사모음 AI 학습 및 활용 금지"
    )
    cleaned = clean_article_text(text)
    assert "충분한 기사 본문" in cleaned
    assert "좋아요" not in cleaned
    assert "최신글" not in cleaned
