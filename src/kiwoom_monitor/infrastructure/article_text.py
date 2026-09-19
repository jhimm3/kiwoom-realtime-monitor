from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


ARTICLE_TEXT_CLEANER_VERSION = "article-text-cleaner-v8"

_VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
_COPYRIGHT_TAIL_MARKERS = (
    "Copyright ⓒ",
    "Copyright ©",
    "All rights reserved",
    "Copyright (C)",
    "무단 전재 및 재배포 금지",
    "무단전재 및 재배포 금지",
    "저작권자ⓒ",
    "저작권자 ⓒ",
    "무단전재·재배포 금지",
    "무단 전재 - 재배포 금지",
    "재판매 및 DB 금지",
)
_UI_TAIL_MARKERS = (
    "MTN 머니투데이방송 다음뉴스",
    "고충처리인 : 콘텐츠총괄부장",
    "[ⓒ 서울경제TV",
    "ⓒ 맛있는 뉴스토마토",
    "주요뉴스 핫포커스 투데이 이슈",
    "유투브 블로그 트위터",
    "메트로신문 지면 PDF 보기",
    "관련기사",
    "※ 여러분의 제보가",
    "[제보]",
    "■ 제보하기",
    "기자의 다른기사",
    "기자의 다른 기사",
    "최신 기사 ▶",
    "이 기자의 최신글",
    "기사모음",
    "정기간행물 등록번호",
    "댓글 더보기 뉴스리듬",
    "댓글 0",
    "©'5개국어 글로벌 경제신문'",
    "이 기사는 언론사에서",
    "기사의 저작권은",
    "AI 학습 및 활용 금지",
    "기사 섹션 분류 안내",
    "기자 프로필",
    "이 기사를 추천합니다",
    "함께 볼만한 뉴스",
    "언론사홈",
    "해당 언론사에서 선정하며",
    "이 기사의 댓글 정책",
    "기사 추천은 24시간",
    "랭킹 뉴스 더보기",
    "네이버 AI 뉴스 알고리즘",
    "이슈 NOW 안내",
    "본문 듣기를 종료",
    "구독하고 메인에서",
    "유튜브, 네이버, 카카오에서도",
    "언론사의 주요 뉴스",
    "주요 뉴스를 메인에서",
    "많이 본 뉴스",
    "랭킹 뉴스",
    "기자 구독",
    "댓글 정책",
    "추천 기사",
    "관련 기사",
    "실시간 인기기사",
    "좋아요 0 나빠요 0",
    "맨위로 예 아니오",
)
_RELATED_SECTION_HEADER = re.compile(r"\[관련 기사\s+\d+/\d+\s*:[^]]*\]")


class _ArticleParser(HTMLParser):
    TARGETS = ("dic_area", "newsct_article", "articlebody", "article_body", "article-view-content-div")

    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self._target_depth: int | None = None
        self._ignored = 0
        self.article: list[str] = []
        self.paragraphs: list[str] = []
        self._in_p = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        is_void = tag in _VOID_ELEMENTS
        if not is_void:
            self._depth += 1
        values = " ".join(value or "" for key, value in attrs if key in {"id", "class"}).casefold()
        if self._target_depth is None and any(target in values for target in self.TARGETS):
            self._target_depth = self._depth
        if tag in {"script", "style", "nav", "header", "footer", "aside"}:
            self._ignored += 1
        if tag == "p":
            self._in_p += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_ELEMENTS:
            return
        if self._target_depth == self._depth:
            self._target_depth = None
        if tag in {"script", "style", "nav", "header", "footer", "aside"} and self._ignored:
            self._ignored -= 1
        if tag == "p" and self._in_p:
            self._in_p -= 1
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        text = " ".join(unescape(data).split())
        if not text:
            return
        if self._target_depth is not None:
            self.article.append(text)
        elif self._in_p:
            self.paragraphs.append(text)


def clean_article_text(text: str) -> str:
    """기사 종료 표식 뒤에 붙은 포털 추천·랭킹·푸터 텍스트를 제거한다."""
    return clean_article_text_with_details(text)[0]


def clean_article_text_with_details(text: str) -> tuple[str, str, int]:
    """정제 본문과 선택된 종료 표식, 원문상 절단 위치를 반환한다."""
    normalized = re.sub(r"\s+", " ", unescape(str(text))).strip()
    headers = list(_RELATED_SECTION_HEADER.finditer(normalized))
    if headers and headers[0].start() == 0:
        sections: list[str] = []
        first_marker = ""
        for index, header in enumerate(headers):
            end = headers[index + 1].start() if index + 1 < len(headers) else len(normalized)
            body, marker, _position = _clean_single_article_text(
                normalized[header.end():end].strip(),
            )
            if body:
                sections.append(f"{header.group(0)} {body}")
            if marker and not first_marker:
                first_marker = marker
        cleaned = " ".join(sections).strip()
        return cleaned, first_marker, len(cleaned)
    return _clean_single_article_text(normalized)


def _clean_single_article_text(normalized: str) -> tuple[str, str, int]:
    if (
        "법인명" in normalized
        and "제호" in normalized
        and ("대표전화" in normalized or "청소년보호책임자" in normalized)
    ):
        return "", "publisher-footer-only", 0
    folded = normalized.casefold()
    cut_at = len(normalized)
    cut_marker = ""
    for marker in _COPYRIGHT_TAIL_MARKERS:
        position = folded.find(marker.casefold(), 1)
        if 0 <= position < cut_at:
            cut_at = position
            cut_marker = marker
    for marker in _UI_TAIL_MARKERS:
        position = folded.find(marker.casefold())
        if 0 <= position < cut_at:
            cut_at = position
            cut_marker = marker
    cleaned = normalized[:cut_at].rstrip()
    if cut_marker and len(re.sub(r"[^0-9A-Za-z가-힣]+", "", cleaned)) < 40:
        return "", cut_marker, 0
    if _looks_like_photo_caption_only(cleaned):
        return "", "photo-caption-only", 0
    return cleaned, cut_marker, cut_at


def _looks_like_photo_caption_only(text: str) -> bool:
    if not text or len(text) > 600:
        return False
    has_credit = bool(re.search(
        r"(?:\([^)]{0,50}사진\s*=|\[사진\s*=|\([^)]{0,30}=뉴스1\)|"
        r"\([^)]{0,30}=연합뉴스\)|기자\s*=|/뉴스1|/연합뉴스)", text,
    ))
    caption_action = bool(re.search(
        r"(?:전광판|현황판|행사|경기|라운드|기념식|출근)[^.!?]{0,160}"
        r"(?:표시되고|박수를\s*치고|포즈를\s*취하고|경기를\s*치르고|들어서고|나서고)\s*있다",
        text,
    ))
    repeated_image = bool(re.search(r"(?:투시도|제품\s*이미지|사진\s*=\s*제공)", text))
    reporting_action = bool(re.search(r"(?:밝혔다|발표했다|공시했다|결정했다|체결했다|설명했다)", text))
    return has_credit and (caption_action or repeated_image) and not reporting_action


def fetch_article_text(url: str, *, timeout_seconds: float = 10.0, max_characters: int = 24_000) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError("기사 원문 주소가 없습니다.")
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
    })
    with urlopen(request, timeout=timeout_seconds, context=system_ssl_context()) as response:
        raw = response.read(2_000_000)
        content_type = response.headers.get_content_charset() if response.headers else None
    html = raw.decode(content_type or "utf-8", errors="replace")
    parser = _ArticleParser()
    parser.feed(html)
    parts = parser.article if sum(map(len, parser.article)) >= 200 else parser.paragraphs
    raw_text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(raw_text) < 120:
        raise ValueError("기사 본문을 추출하지 못했습니다. 원문 페이지에서 확인하세요.")
    text = clean_article_text(raw_text)
    if len(text) < 40:
        raise ValueError("기사 본문을 추출하지 못했습니다. 원문 페이지에서 확인하세요.")
    return text[:max_characters]
