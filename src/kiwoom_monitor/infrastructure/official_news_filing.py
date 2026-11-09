"""Fetch the exact public DART or KIND document behind a collected filing URL."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from kiwoom_monitor.application.news_material import official_adverse_kind, official_filing_ref
from kiwoom_monitor.infrastructure.exchange_effective_dates import document_rows
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


MAX_FILING_BYTES = 2_000_000
KIND_DOCUMENT_UNVERIFIED = "KIND document number is unverified"
_DART = "https://dart.fss.or.kr"
_KIND = "https://kind.krx.co.kr"
_DART_MAIN = _DART + "/dsaf001/main.do?rcpNo="
_KIND_CONTENT = re.compile(r"parent\.setPath\(\s*'([^'\r\n]*)'\s*,\s*'(https://kind\.krx\.co\.kr/external/[\w/.\-]+\.htm)'")
_OPTION = re.compile(r"<option\s+value=['\"]([0-9]{14})\|[^'\"]*['\"]([^>]*)>(.*?)</option>", re.S)
_FAMILY = re.compile(r'<select\b[^>]*\bid="doc"[^>]*>(.*?)</select>', re.S)
_DART_OPTION = re.compile(r'<option\b[^>]*value="rcpNo=([0-9]{14})"[^>]*title="([^"]*)"', re.S)
_DART_VIEW = re.compile(r'''viewDoc\(\s*["']([0-9]{14})["'],\s*["'](\d+)["'],\s*["'](\d+)["'],'''
                        r'''\s*["'](\d+)["'],\s*["'](\d+)["'],\s*["']([\w.\-]+)["']''')
_RELATED_FILING = re.compile(r"<a\b[^>]*href=['\"]([^'\"]+)['\"][^>]*>([^<]*공급계약\s*체결[^<]*)</a>", re.S)
_FILING_HEADING = re.compile(r"<(title|span|h[1-3]|p)\b[^>]*>(.{1,512}?)</\1>", re.I | re.S)


def confirms_adverse_filing(html: str, expected_kind: str) -> bool:
    for tag, value in _FILING_HEADING.findall(html[:32_000]):
        heading = unescape(re.sub(r"<[^>]+>", "", value)).strip()
        if tag.lower() == "title":
            heading = re.sub(r"^::\s*\d+_", "", heading)
        if official_adverse_kind(heading) == expected_kind:
            return True
    return False


def _read(url: str, timeout: float) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 KiwoomRealtimeMonitor/1.1"})
    with urlopen(request, timeout=timeout, context=system_ssl_context()) as response:
        if urlsplit(response.geturl()).hostname != urlsplit(url).hostname:
            raise ValueError("official filing changed host")
        raw = response.read(MAX_FILING_BYTES + 1)
        encoding = response.headers.get_content_charset() or "utf-8"
    if not raw or len(raw) > MAX_FILING_BYTES:
        raise ValueError("official filing size is outside the BODY contract")
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        return raw.decode("cp949")


def _kind_content(doc_no: str, read) -> tuple[str, bool]:
    page = read(_KIND + "/common/disclsviewer.do?method=searchContents&docNo=" + doc_no)
    # setPath's first argument is an optional table of contents. The second is the filing body.
    paths = _KIND_CONTENT.findall(page)
    targets = {body for _, body in paths}
    if len(targets) != 1:
        raise ValueError(KIND_DOCUMENT_UNVERIFIED)
    target, = targets
    if official_filing_ref(target) != f"kind:{doc_no}":
        raise ValueError(KIND_DOCUMENT_UNVERIFIED)
    toc = target.removesuffix(".htm") + "_toc.htm"
    # If an empty first argument also exists, the previous parser could read it.
    return target, all(index == toc for index, _ in paths)


def _kind_document(url: str, receipt: str, read) -> tuple[str, str, bool]:
    if urlsplit(url).path.startswith("/external/"):
        return _KIND + urlsplit(url).path, "", False
    view = read(_KIND + "/common/disclsviewer.do?method=search&acptno=" + receipt)
    options = _OPTION.findall(view)
    selected = [(doc, title) for doc, flags, title in options if "selected" in flags]
    if not selected and len(options) == 1:
        selected = [(options[0][0], options[0][2])]
    if len(selected) != 1:
        raise ValueError("KIND requested document selection is unverified")
    doc, title = selected[0]
    target, has_toc = _kind_content(doc, read)
    if urlsplit(target).path.split("/")[5] != receipt[-6:]:
        raise ValueError("KIND document does not match the requested receipt")
    parents = [key for key, _, name in options if key != doc and not name.strip().startswith("[")
               and re.search(r"공급계약\s*(?:해지|해제)", name)]
    parent, parent_has_toc = (
        _kind_content(parents[0], read) if title.strip().startswith("[") and len(parents) == 1 else ("", False)
    )
    return target, parent, has_toc or parent_has_toc


def _dart_document(receipt: str, read) -> tuple[str, str]:
    view = read(_DART_MAIN + receipt)
    matches = {match for match in _DART_VIEW.findall(view) if match[0] == receipt}
    if len(matches) != 1:
        raise ValueError("DART requested document selection is unverified")
    values = next(iter(matches))
    if values[-1] != "HTML" and view.count("treeData.push(node1)") > 1:
        raise ValueError("DART sectioned filing requires a complete source document")
    target = _DART + "/report/viewer.do?" + urlencode(dict(zip(("rcpNo", "dcmNo", "eleId", "offset", "length", "dtd"), values)))
    family = _FAMILY.search(view)
    parents = [key for key, title in _DART_OPTION.findall(family[1]) if key < receipt
               and re.search(r"공급계약\s*(?:해지|해제)", title)] if family else []
    return target, _DART_MAIN + min(parents) if parents else ""


def _filing_body(url: str, target: str, parent: str, read) -> dict[str, str]:
    body = read(target)
    if not document_rows(body) and not re.search(r"<body\b[^>]*>\s*\S.+?</body>", body, re.I | re.S):
        raise ValueError("official filing document is empty")
    if not parent:
        links = set()
        for href, _ in _RELATED_FILING.findall(body):
            link = unescape(href)
            if link.startswith("/"):
                link = (_DART if urlsplit(url).hostname == "dart.fss.or.kr" else _KIND) + link
            if official_filing_ref(link) and official_filing_ref(link) != official_filing_ref(url):
                links.add(link)
        if len(links) == 1:
            parent = next(iter(links))
    return {"filing_url": url, "document_url": target, "filing_html": body, "original_filing_url": parent}


def fetch_official_filing(url: str, *, timeout_seconds: float) -> dict[str, str]:
    provider, receipt = official_filing_ref(url).split(":")

    def read(target: str) -> str:
        return _read(target, timeout_seconds)

    if provider == "dart":
        target, parent = _dart_document(receipt, read)
    else:
        target, parent, _ = _kind_document(url, receipt, read)
    return _filing_body(url, target, parent, read)
