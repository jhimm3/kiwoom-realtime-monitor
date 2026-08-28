from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


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
        self._depth += 1
        values = " ".join(value or "" for key, value in attrs if key in {"id", "class"}).casefold()
        if self._target_depth is None and any(target in values for target in self.TARGETS):
            self._target_depth = self._depth
        if tag in {"script", "style", "nav", "header", "footer", "aside"}:
            self._ignored += 1
        if tag == "p":
            self._in_p += 1

    def handle_endtag(self, tag: str) -> None:
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
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(text) < 120:
        raise ValueError("기사 본문을 추출하지 못했습니다. 원문 페이지에서 확인하세요.")
    return text[:max_characters]
