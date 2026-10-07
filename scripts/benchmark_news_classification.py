"""고정한 NAS 뉴스 corpus에서 기준 라벨과 제품 분류 결과를 비교한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.article_text import (
    ARTICLE_TEXT_CLEANER_VERSION,
    clean_article_text_with_details,
)


REFERENCE_VERSION = "news-audit-reference-v11"

_CORPORATE_FACTS = (
    ("수주·계약", re.compile(r"공급\s*계약|계약\s*(?:체결|해지|취소)|수주", re.I)),
    ("실적·전망", re.compile(
        r"실적\s*(?:발표|공시)|(?:매출|영업이익|영업익|순이익|영업손실|순손실)"
        r"[^.!?]{0,35}(?:증가|감소|급증|급감|흑자|적자|전환|전망|추정|상향|하향)|"
        r"흑자\s*전환|적자\s*전환|전망치|추정치|컨센서스|가이던스", re.I,
    )),
    ("자본·주주환원", re.compile(
        r"유증|유상증자|무상증자|감자\s*결정|전환사채|자사주\s*(?:취득|소각)|배당\s*(?:결정|확대|축소)", re.I,
    )),
    ("투자·인수합병", re.compile(
        r"투자\s*결정|시설투자|설비투자|인수\s*(?:결정|계약|완료)|합병\s*(?:결정|계약|승인)|지분\s*취득", re.I,
    )),
    ("임상·허가", re.compile(
        r"임상[^.!?]{0,25}(?:성공|실패|결과|승인|신청|중단)|품목허가|FDA\s*(?:승인|신청|거절|보완요구)|특허\s*(?:취득|등록|소송)", re.I,
    )),
    ("경영권·주주", re.compile(
        r"최대주주\s*(?:변경|매각)|대표이사\s*(?:선임|사임)|경영권\s*(?:분쟁|인수)", re.I,
    )),
    ("공시·규제", re.compile(r"거래정지|상장폐지|횡령|배임|압수수색|소송|제재|리콜", re.I)),
    ("기타 증권뉴스", re.compile(r"(?:개발\s*)?사업[^.!?]{0,25}참여|사업\s*참여", re.I)),
)
_ANALYST_FACT = re.compile(r"목표주가|목표가|투자의견|증권사[^.!?]{0,30}(?:전망|분석|평가)", re.I)
_MARKET_SUBJECT = re.compile(r"코스피|코스닥|증시|지수선물|선물옵션|원/달러|환율|국제유가", re.I)
_PRICE_MOVE = re.compile(r"급등|급락|강세|약세|강보합|약보합|보합|상한가|하한가|상승|하락|반등|폭등|폭락|탈환|붕괴|밀려|치솟", re.I)
_MARKET_WRAP = re.compile(r"\[(?:장중시황|마감시황|시황|코스피[^]]*|코스닥[^]]*)\]|장중|장초반|마감", re.I)
_MARKET_RECAP = re.compile(r"\[(?:베스트\s*&\s*워스트|주식마감|장중시황|마감시황)[^]]*\]", re.I)
_PRICE_NUMBER = re.compile(r"\d[\d,.]*\s*(?:%|원|선)|상한가|하한가", re.I)
_SECURITIES_CONTEXT = re.compile(
    r"주가|증시|공시|실적|매출|영업이익|영업익|순이익|수주|계약|투자|인수|합병|"
    r"증자|감자|전환사채|자사주|배당|임상|허가|특허|최대주주|목표주가|투자의견|"
    r"상장|거래정지|기업가치|시가총액",
    re.I,
)
_MATERIAL_BUSINESS = re.compile(
    r"(?:제품|솔루션|기술)[^.!?]{0,30}(?:출시|공개|개발|적용)|"
    r"(?:첫|신규)\s*진출|신사업[^.!?]{0,25}(?:진출|출범|공개)|"
    r"(?:수출|판매)[^.!?]{0,25}(?:\d+(?:\.\d+)?\s*(?:%|↑|↓)|급증|증가|감소)|"
    r"\d[\d,.]*(?:억|조)원?[^.!?]{0,30}(?:투입|투자|베팅|공급|납품)|"
    r"(?:공장|설비|생산)[^.!?]{0,30}(?:증설|확대|감축|중단|재개)|"
    r"(?:착공|준공|양산\s*(?:개시|시작)|공급망[^.!?]{0,20}(?:차질|중단))",
    re.I,
)
_LOW_VALUE_COMPANY_NEWS = re.compile(
    r"(?:할인|쿠폰|경품)\s*(?:행사|이벤트)|사회공헌|봉사활동|기부금?\s*전달|"
    r"채용\s*(?:설명회|박람회)|시승기|아파트[^.!?]{0,30}(?:인접|분양)|"
    r"(?:야구|축구|골프|배구|드라마|예능)[^.!?]{0,30}(?:경기|대회|방송|우승)|"
    r"납품대금[^.!?]{0,25}조기\s*지급|노조[^.!?]{0,40}납품[^.!?]{0,20}특혜",
    re.I,
)
_GENERIC_MOU = re.compile(r"MOU|업무협약", re.I)
_SOCIAL_MOU = re.compile(r"교육|봉사|사회공헌|서비스|복지|지원사업|캠페인|상생", re.I)
_FRESH = re.compile(r"오늘|금일|이날|당일|발표했다|공시했다|결정했다|체결했다", re.I)
_STALE = re.compile(r"지난달|지난해|작년|과거|당시|\d+\s*(?:일|개월|년)\s*전", re.I)
_NON_STOCK_MOVE = re.compile(
    r"(?:환율|원화|달러|엔화|유가|원유|금리|국채금리|수익률|실적\s*전망|영업이익\s*전망|"
    r"영업익\s*전망|순이익\s*전망|순익\s*전망|매출\s*전망|전망치|추정치|컨센서스|"
    r"판매량?|수출|점유율|사용량|재이용량|생산량)"
    r"[^,.!?]{0,24}(?:급등|급락|강세|약세|상승|하락|반등|폭등|폭락|뚝|치솟|증발|↑|↓)",
    re.I,
)
_FRESH_ANALYST_CHANGE = re.compile(
    r"투자의견[^.!?]{0,35}(?:상향|하향|매수|매도)|목표(?:주가|가)[^.!?]{0,35}(?:상향|하향|제시)", re.I,
)
_FRESH_MATERIAL_TITLE = re.compile(r"발표|공시|결정|체결|착수|참여|진출", re.I)


@dataclass(frozen=True)
class ReferenceLabel:
    decision: str
    category: str
    reason: str
    confidence: str


def reference_label(stock_name: str, title: str, description: str, body: str) -> ReferenceLabel:
    title_text = _plain(title)
    description_text = _plain(description)
    lead = _plain(body)[:1200]
    combined = f"{title_text} {description_text} {lead}"
    compact_name = _compact(stock_name)
    direct_title = bool(compact_name and compact_name in _compact(title_text))
    direct_summary = bool(compact_name and compact_name in _compact(f"{title_text} {description_text}"))
    direct_lead = bool(compact_name and compact_name in _compact(lead[:700]))
    direct = direct_summary or direct_lead

    if direct_summary and _LOW_VALUE_COMPANY_NEWS.search(f"{title_text} {description_text}"):
        return ReferenceLabel("DROP", "비투자성 기업 소식", "행사·홍보·생활 정보", "MEDIUM")
    if _MARKET_RECAP.search(title_text):
        return ReferenceLabel("DROP", "시세 반영·시장 요약", "기간별 등락·다종목 시황 정리", "HIGH")
    if (
        compact_name
        and _compact(title_text).endswith(f"-{compact_name}")
        and _compact(description_text).startswith(f"{compact_name}증권")
    ):
        return ReferenceLabel("DROP", "관련성 낮음", "종목명이 아니라 증권사 출처 표기", "HIGH")

    # 제목에 적힌 새 기업 사실을 가격 반응보다 우선한다. 회사 정식명이
    # 요약에만 있어도 제목의 사건 주체가 확인되면 별칭 제목으로 본다.
    for category, pattern in _CORPORATE_FACTS:
        if direct_title and pattern.search(title_text):
            return ReferenceLabel("KEEP", category, "기업 사건 또는 실적 사실", "HIGH")
    if direct_title and _ANALYST_FACT.search(title_text):
        return ReferenceLabel("KEEP", "실적·전망", "목표주가·투자의견·분석 변경", "HIGH")
    if direct_title and _MATERIAL_BUSINESS.search(title_text) and (
        not _PRICE_MOVE.search(title_text) or _FRESH_MATERIAL_TITLE.search(title_text)
    ):
        return ReferenceLabel("KEEP", "기타 증권뉴스", "제목에 확인 가능한 사업 사건", "HIGH")
    lead_opening = lead[:900]
    if direct_title and _FRESH_ANALYST_CHANGE.search(lead_opening) and not _STALE.search(lead_opening[:300]):
        return ReferenceLabel("KEEP", "실적·전망", "본문 도입부의 투자의견·목표가 변경", "HIGH")

    equity_move_title = _NON_STOCK_MOVE.sub(" ", title_text)
    has_move = bool(_PRICE_MOVE.search(equity_move_title))
    if has_move and _MARKET_SUBJECT.search(equity_move_title) and (
        _MARKET_WRAP.search(title_text) or _PRICE_NUMBER.search(title_text)
    ):
        return ReferenceLabel("DROP", "시세 반영·시장 요약", "시장·지수 움직임 중계", "HIGH")
    if has_move and direct and (_MARKET_WRAP.search(title_text) or _PRICE_NUMBER.search(title_text)):
        return ReferenceLabel("DROP", "시세 반영·시장 요약", "이미 발생한 종목 가격 움직임", "HIGH")
    if direct_title and _GENERIC_MOU.search(title_text):
        if _SOCIAL_MOU.search(f"{title_text} {description_text}"):
            return ReferenceLabel("DROP", "비투자성 기업 소식", "사회공헌·교육·서비스 협약", "MEDIUM")
        return ReferenceLabel("KEEP", "수주·계약", "사업 협력 사실", "MEDIUM")
    if stock_name and not direct:
        return ReferenceLabel("DROP", "관련성 낮음", "제목·검색요약에 대상 회사 직접 언급 없음", "MEDIUM")
    if direct_title and (
        _SECURITIES_CONTEXT.search(f"{title_text} {description_text}")
        or _MATERIAL_BUSINESS.search(f"{title_text} {description_text}")
    ):
        return ReferenceLabel("KEEP", "기타 증권뉴스", "대상 회사의 사업·증권 문맥", "MEDIUM")
    if not stock_name and has_move and (_MARKET_SUBJECT.search(title_text) or _MARKET_WRAP.search(title_text)):
        return ReferenceLabel("DROP", "시세 반영·시장 요약", "종목 미지정 시장 움직임", "HIGH")
    if stock_name:
        return ReferenceLabel("DROP", "관련성 낮음", "투자 판단에 쓸 직접 사건 근거 부족", "LOW")
    for category, pattern in _CORPORATE_FACTS:
        if pattern.search(title_text):
            return ReferenceLabel("KEEP", category, "종목 미지정 기업·증권 사건", "MEDIUM")
    if _SECURITIES_CONTEXT.search(f"{title_text} {description_text}"):
        return ReferenceLabel("KEEP", "일반 증권뉴스", "종목 미지정 증권 사건", "LOW")
    return ReferenceLabel("DROP", "비증권 뉴스", "투자 판단에 쓸 증권 근거 부족", "LOW")


def build_and_run(database_path: Path) -> dict[str, Any]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        records = _records(connection)
        articles = records["history:article"]
        bodies = records["history:body"]
        projections = records["content:news_article"]
        stock_names = _stock_names(records)
        latest_articles: dict[tuple[str, str], dict[str, Any]] = {}
        latest_any: dict[str, dict[str, Any]] = {}
        for article in articles:
            identity = str(article.get("identity") or "")
            stock_code = str(article.get("stock_code") or "")
            _keep_latest(latest_articles, (stock_code, identity), article)
            _keep_latest(latest_any, identity, article)
        latest_bodies: dict[str, dict[str, Any]] = {}
        for body in bodies:
            _keep_latest(latest_bodies, str(body.get("article_revision_id") or ""), body)

        items: dict[str, dict[str, Any]] = {}
        represented_identities: set[str] = set()
        for row in projections:
            document = row.get("document") or {}
            stock_code = str(row.get("owner") or "")
            identity = _document_identity(document)
            article = latest_articles.get((stock_code, identity)) or latest_articles.get(("GLOBAL", identity)) or latest_any.get(identity)
            item = _evaluation_item(stock_code, stock_names.get(stock_code, ""), identity, document, article, latest_bodies)
            items[item["item_id"]] = item
            represented_identities.add(identity)
        for identity, article in latest_any.items():
            if identity in represented_identities:
                continue
            document = article.get("document") or {}
            stock_code = str(article.get("stock_code") or "")
            item = _evaluation_item(stock_code, stock_names.get(stock_code, ""), identity, document, article, latest_bodies)
            items[item["item_id"]] = item

        connection.executescript(
            "DROP TABLE IF EXISTS news_cleaned_bodies; DROP TABLE IF EXISTS news_audit_items; "
            "DROP TABLE IF EXISTS news_reference_labels;"
            "CREATE TABLE news_cleaned_bodies("
            " body_revision_id TEXT PRIMARY KEY,article_revision_id TEXT,status TEXT,effective_status TEXT,cleaner_version TEXT,"
            " raw_body_hash TEXT,clean_body_hash TEXT,raw_body_length INTEGER,clean_body_length INTEGER,"
            " cut_marker TEXT,clean_body_text TEXT);"
            "CREATE TABLE news_audit_items("
            " item_id TEXT PRIMARY KEY,identity TEXT,stock_code TEXT,stock_name TEXT,article_revision_id TEXT,"
            " body_revision_id TEXT,title TEXT,description TEXT,body_status TEXT,published_at TEXT,"
            " raw_body_length INTEGER,clean_body_length INTEGER,raw_body_hash TEXT,clean_body_hash TEXT,"
            " cut_marker TEXT,cleaner_version TEXT,payload_json TEXT);"
            "CREATE TABLE news_reference_labels("
            " item_id TEXT PRIMARY KEY,reference_version TEXT,decision TEXT,category TEXT,reason TEXT,confidence TEXT);"
            "CREATE TABLE IF NOT EXISTS news_candidate_runs("
            " run_id TEXT,item_id TEXT,candidate_version TEXT,relevant INTEGER,category TEXT,outlook TEXT,reason TEXT,"
            " PRIMARY KEY(run_id,item_id));"
            "CREATE TABLE IF NOT EXISTS news_benchmark_runs("
            " run_id TEXT PRIMARY KEY,candidate_version TEXT,created_at REAL,summary_json TEXT);"
        )
        cleaned_bodies: dict[str, tuple[str, str]] = {}
        body_ledger_changed = body_ledger_raw_chars = body_ledger_clean_chars = 0
        body_ledger_unusable_fulltext = 0
        body_ledger_markers: Counter[str] = Counter()
        for body_row in bodies:
            body_revision_id = str(body_row.get("body_revision_id") or "")
            raw_body = str(body_row.get("body_text") or "")
            clean_body, cut_marker, _cut_at = clean_article_text_with_details(raw_body)
            original_status = str(body_row.get("status") or "")
            effective_status = "unusable" if original_status == "fulltext" and not clean_body else original_status
            body_ledger_unusable_fulltext += effective_status == "unusable"
            cleaned_bodies[body_revision_id] = (clean_body, cut_marker)
            body_ledger_raw_chars += len(raw_body)
            body_ledger_clean_chars += len(clean_body)
            body_ledger_changed += clean_body != raw_body
            if cut_marker:
                body_ledger_markers[cut_marker] += 1
            connection.execute(
                "INSERT INTO news_cleaned_bodies VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    body_revision_id, str(body_row.get("article_revision_id") or ""),
                    original_status, effective_status, ARTICLE_TEXT_CLEANER_VERSION,
                    hashlib.sha256(raw_body.encode()).hexdigest() if raw_body else "",
                    hashlib.sha256(clean_body.encode()).hexdigest() if clean_body else "",
                    len(raw_body), len(clean_body), cut_marker, clean_body,
                ),
            )
        references: dict[str, ReferenceLabel] = {}
        cleaned_count = raw_body_characters = clean_body_characters = 0
        cut_markers: Counter[str] = Counter()
        for item in items.values():
            raw_body = item["body"]
            cleaned = cleaned_bodies.get(item["body_revision_id"])
            if cleaned is None:
                clean_body, cut_marker = clean_article_text_with_details(raw_body)[:2]
            else:
                clean_body, cut_marker = cleaned
            item["clean_body"] = clean_body
            raw_body_characters += len(raw_body)
            clean_body_characters += len(clean_body)
            cleaned_count += clean_body != raw_body
            if cut_marker:
                cut_markers[cut_marker] += 1
            reference = reference_label(
                item["stock_name"], item["title"], item["description"], clean_body,
            )
            references[item["item_id"]] = reference
            payload = dict(item)
            body = payload.pop("body")
            payload.pop("clean_body")
            payload["body_length"] = len(body)
            connection.execute(
                "INSERT INTO news_audit_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["item_id"], item["identity"], item["stock_code"], item["stock_name"],
                    item["article_revision_id"], item["body_revision_id"], item["title"], item["description"],
                    item["body_status"], item["published_at"], len(body), len(clean_body),
                    hashlib.sha256(body.encode()).hexdigest() if body else "",
                    hashlib.sha256(clean_body.encode()).hexdigest() if clean_body else "",
                    cut_marker, ARTICLE_TEXT_CLEANER_VERSION, json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.execute(
                "INSERT INTO news_reference_labels VALUES(?,?,?,?,?,?)",
                (item["item_id"], REFERENCE_VERSION, reference.decision, reference.category,
                 reference.reason, reference.confidence),
            )

        candidate_version = _candidate_version()
        run_id = hashlib.sha256(
            f"{candidate_version}\0{REFERENCE_VERSION}\0{time.time_ns()}".encode()
        ).hexdigest()[:20]
        comparable = agreements = false_keep = false_drop = category_match = category_total = 0
        confidence_metrics: dict[str, dict[str, int]] = {}
        candidate_count = 0
        for item in items.values():
            if not item["stock_name"]:
                continue
            candidate = assess_stock_news(
                item["stock_name"], item["title"], item["description"], article_body=item["clean_body"],
            )
            candidate_count += 1
            connection.execute(
                "INSERT INTO news_candidate_runs VALUES(?,?,?,?,?,?,?)",
                (run_id, item["item_id"], candidate_version, int(candidate.relevant), candidate.category,
                 candidate.outlook, candidate.reason),
            )
            reference = references[item["item_id"]]
            if reference.decision == "REVIEW":
                continue
            comparable += 1
            expected = reference.decision == "KEEP"
            agreements += candidate.relevant == expected
            false_keep += bool(candidate.relevant and not expected)
            false_drop += bool(not candidate.relevant and expected)
            metric = confidence_metrics.setdefault(reference.confidence, {
                "comparable": 0, "agreement": 0, "false_keep": 0, "false_drop": 0,
            })
            metric["comparable"] += 1
            metric["agreement"] += int(candidate.relevant == expected)
            metric["false_keep"] += int(candidate.relevant and not expected)
            metric["false_drop"] += int(not candidate.relevant and expected)
            if expected and candidate.relevant:
                category_total += 1
                category_match += candidate.category == reference.category or reference.category == "기타 증권뉴스"

        reference_counts: dict[str, int] = {}
        for value in references.values():
            key = f"{value.decision}:{value.confidence}"
            reference_counts[key] = reference_counts.get(key, 0) + 1
        summary = {
            "run_id": run_id,
            "reference_version": REFERENCE_VERSION,
            "candidate_version": candidate_version,
            "total_items": len(items),
            "candidate_items": candidate_count,
            "body_cleaning": {
                "changed_items": cleaned_count,
                "raw_characters": raw_body_characters,
                "clean_characters": clean_body_characters,
                "removed_characters": raw_body_characters - clean_body_characters,
                "removed_rate": round(
                    (raw_body_characters - clean_body_characters) / raw_body_characters, 6,
                ) if raw_body_characters else 0.0,
                "cut_markers": dict(cut_markers.most_common()),
            },
            "body_ledger": {
                "body_revisions": len(bodies),
                "changed_revisions": body_ledger_changed,
                "unusable_fulltext_revisions": body_ledger_unusable_fulltext,
                "raw_characters": body_ledger_raw_chars,
                "clean_characters": body_ledger_clean_chars,
                "removed_characters": body_ledger_raw_chars - body_ledger_clean_chars,
                "removed_rate": round(
                    (body_ledger_raw_chars - body_ledger_clean_chars) / body_ledger_raw_chars, 6,
                ) if body_ledger_raw_chars else 0.0,
                "cut_markers": dict(body_ledger_markers.most_common()),
            },
            "reference_counts": reference_counts,
            "comparable_items": comparable,
            "agreement_count": agreements,
            "agreement_rate": round(agreements / comparable, 6) if comparable else None,
            "false_keep": false_keep,
            "false_drop": false_drop,
            "by_confidence": {
                confidence: {
                    **metric,
                    "agreement_rate": round(metric["agreement"] / metric["comparable"], 6),
                }
                for confidence, metric in sorted(confidence_metrics.items())
            },
            "category_comparable": category_total,
            "category_agreement_rate": round(category_match / category_total, 6) if category_total else None,
        }
        connection.execute(
            "INSERT INTO news_benchmark_runs VALUES(?,?,?,?)",
            (run_id, candidate_version, time.time(), json.dumps(summary, ensure_ascii=False)),
        )
        connection.commit()
    return summary


def _records(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute("SELECT kind,payload_json FROM records"):
        result.setdefault(str(row[0]), []).append(json.loads(str(row[1])))
    return result


def _stock_names(records: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    result = {}
    for row in records.get("content:news_watchlist", []):
        document = row.get("document") or {}
        code, name = str(document.get("stock_code") or row.get("owner") or ""), str(document.get("stock_name") or "")
        if code and name:
            result[code] = name
    for row in records.get("content:stock_fundamentals", []):
        document = row.get("document") or {}
        payload = document.get("payload") or {}
        code = str(document.get("code") or row.get("owner") or "")
        name = str(payload.get("stk_nm") or document.get("name") or "")
        if code and name:
            result[code] = name
    return result


def _keep_latest(target: dict[Any, dict[str, Any]], key: Any, value: dict[str, Any]) -> None:
    if not key:
        return
    current = target.get(key)
    if current is None or float(value.get("available_at") or 0) > float(current.get("available_at") or 0):
        target[key] = value


def _evaluation_item(stock_code: str, stock_name: str, identity: str, document: dict[str, Any],
                     article: dict[str, Any] | None, bodies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    article_revision_id = str((article or {}).get("article_revision_id") or "")
    body = bodies.get(article_revision_id, {})
    item_id = hashlib.sha256(f"{stock_code}\0{identity}".encode()).hexdigest()
    return {
        "item_id": item_id, "identity": identity, "stock_code": stock_code,
        "stock_name": stock_name, "article_revision_id": article_revision_id,
        "body_revision_id": str(body.get("body_revision_id") or ""),
        "title": str(document.get("title") or ""),
        "description": str(document.get("description") or ""),
        "body": str(body.get("body_text") or "") if body.get("status") == "fulltext" else "",
        "body_status": str(body.get("status") or "missing"),
        "published_at": str(document.get("published_at") or (article or {}).get("published_at") or ""),
    }


def _document_identity(document: dict[str, Any]) -> str:
    return str(document.get("original_link") or document.get("link") or document.get("identity") or "")


def _candidate_version() -> str:
    from kiwoom_monitor.application import news_analysis
    return (
        "news-analysis:" + hashlib.sha256(Path(news_analysis.__file__).read_bytes()).hexdigest()[:16]
        + "+" + ARTICLE_TEXT_CLEANER_VERSION
    )


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(value))).strip()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build_and_run(arguments.database), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
