"""Audit direction evidence missed by the production lead-window heuristic.

This script reads a frozen corpus produced by benchmark_news_classification.py.
It does not modify production data or labels.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from kiwoom_monitor.application.news_analysis import (
    _BODY_NEGATIVE_EVIDENCE,
    _BODY_POSITIVE_EVIDENCE,
    _MATERIAL_BUSINESS_EVENT,
    _SUBSTANTIVE_EVENT,
    _direction_terms,
)


CURRENT_DIRECTION_WINDOW = 350
LEAD_WINDOW = 700
_STALE = re.compile(
    r"지난달|지난해|작년|과거|당시|앞서|종전|기존|이미|"
    r"(?:올해|금년|지난)\s*\d{1,2}월|\d{4}년|\d+\s*(?:일|개월|년)\s*전",
    re.I,
)
_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_NEGATED_OR_SPECULATIVE = re.compile(
    r"유상증자[^.!?]{0,18}(?:제외|하지|아니)|배당\s*확대[^.!?]{0,18}(?:어렵|곤란)|"
    r"(?:수주|계약)[^.!?]{0,24}(?:기대|예상|전망|계획|목표|방침|가능성|나설|나서|"
    r"연결되는지|대응할|추진|확정된\s*사안은\s*아니)|수주전|수주\s*단계|"
    r"목표(?:주가|가)[^.!?]{0,18}유지",
    re.I,
)
_ASSERTED_EVENT = re.compile(
    r"(?:공급\s*계약|계약|수주)[^.!?]{0,32}(?:체결|맺|확정|성사|공시|완료|성공)|"
    r"(?:체결|맺|확정|성사|공시|완료|성공)[^.!?]{0,32}(?:공급\s*계약|계약|수주)|"
    r"(?:흑자|적자)\s*전환[^.!?]{0,24}(?:성공|기록|했다|됐다)|"
    r"(?:매출|영업이익|영업익|순이익|순익)[^.!?]{0,35}(?:증가|급증|감소|급감|개선|악화)|"
    r"(?:유상증자|무상증자|감자|자사주\s*(?:취득|소각)|배당\s*(?:확대|축소))"
    r"[^.!?]{0,30}(?:결정|공시|단행|추진|완료)|"
    r"(?:목표주가|목표가|투자의견)[^.!?]{0,24}(?:상향|하향|매수|매도)|"
    r"(?:승인|허가|특허)[^.!?]{0,25}(?:받|획득|취득|등록|취소|거절)|"
    r"(?:거래정지|상장폐지|횡령|배임|압수수색|피소|소송\s*제기|리콜|제재)|"
    r"(?:계약\s*해지|수주\s*취소|영업손실|순손실|임상\s*(?:성공|실패))",
    re.I,
)


@dataclass(frozen=True)
class MissedEvidence:
    item_id: str
    stock_name: str
    title: str
    offset: int
    polarity: str
    terms: tuple[str, ...]
    sentence: str
    reference_decision: str
    reference_confidence: str


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _direct_company_match(stock_name: str, sentence: str) -> re.Match[str] | None:
    name = re.sub(r"\s+", r"\\s*", re.escape(stock_name.strip()))
    if not name:
        return None
    # 짧은 이름(GS 등)이 GS건설 같은 다른 회사명의 접두사로 잡히지 않게 한다.
    suffix = r"(?=$|[\s,()\[\]·]|은|는|이|가|을|를|의|에|에서|으로|로|와|과|도|만|측)"
    return re.search(name + suffix, sentence, re.I)


def _sentence_spans(body: str) -> tuple[tuple[int, str], ...]:
    return tuple((match.start(), match.group().strip()) for match in _SENTENCE.finditer(body) if match.group().strip())


def _direct_direction_evidence(stock_name: str, body: str) -> tuple[MissedEvidence, ...]:
    if not stock_name.strip():
        return ()
    matches: list[MissedEvidence] = []
    for offset, sentence in _sentence_spans(body):
        company = _direct_company_match(stock_name, sentence)
        if offset < CURRENT_DIRECTION_WINDOW or company is None:
            continue
        positive, negative = _direction_terms(sentence.casefold())
        positive = [term for term in positive if term in _BODY_POSITIVE_EVIDENCE]
        negative = [term for term in negative if term in _BODY_NEGATIVE_EVIDENCE]
        if not positive and not negative:
            continue
        if _STALE.search(sentence) or _NEGATED_OR_SPECULATIVE.search(sentence):
            continue
        asserted = _ASSERTED_EVENT.search(sentence)
        if asserted is None:
            continue
        # 다종목·산업 기사에서 다른 회사 사건 뒤에 대상 종목명이 비교 대상으로만
        # 등장하는 경우를 제외한다. 대상 회사가 먼저 나오고 가까운 사건만 인정한다.
        if company.start() > asserted.start() or asserted.start() - company.end() > 120:
            continue
        polarity = "mixed" if positive and negative else "positive" if positive else "negative"
        matches.append(MissedEvidence("", stock_name, "", offset, polarity, tuple(positive + negative), sentence, "", ""))
    return tuple(matches)


def audit(database: Path) -> dict[str, object]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        run = connection.execute(
            "SELECT run_id,candidate_version FROM news_benchmark_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if run is None:
            raise RuntimeError("benchmark_news_classification.py를 먼저 실행해야 합니다.")
        rows = connection.execute(
            "SELECT i.item_id,i.stock_name,i.title,i.body_status,b.clean_body_text,"
            "r.decision AS reference_decision,r.confidence AS reference_confidence "
            "FROM news_candidate_runs c "
            "JOIN news_audit_items i ON i.item_id=c.item_id "
            "LEFT JOIN news_cleaned_bodies b ON b.body_revision_id=i.body_revision_id "
            "LEFT JOIN news_reference_labels r ON r.item_id=i.item_id "
            "WHERE c.run_id=? AND c.outlook='판단 보류'",
            (run["run_id"],),
        ).fetchall()

    fulltext_rows = [row for row in rows if row["body_status"] == "fulltext" and str(row["clean_body_text"] or "")]
    direct_after_350 = direct_after_700 = 0
    candidates: list[MissedEvidence] = []
    item_candidates: set[str] = set()
    polarity = Counter()
    offset_bands = Counter()
    reference = Counter()
    for row in fulltext_rows:
        body = str(row["clean_body_text"] or "")
        name = _compact(str(row["stock_name"] or ""))
        if name and name in _compact(body[CURRENT_DIRECTION_WINDOW:]):
            direct_after_350 += 1
        if name and name in _compact(body[LEAD_WINDOW:]):
            direct_after_700 += 1
        evidence = _direct_direction_evidence(str(row["stock_name"] or ""), body)
        if not evidence:
            continue
        item_candidates.add(str(row["item_id"]))
        first = evidence[0]
        enriched = MissedEvidence(
            str(row["item_id"]), first.stock_name, str(row["title"] or ""), first.offset,
            first.polarity, first.terms, first.sentence[:500],
            str(row["reference_decision"] or ""), str(row["reference_confidence"] or ""),
        )
        candidates.append(enriched)
        polarity[enriched.polarity] += 1
        offset_bands[
            "350-699" if enriched.offset < 700 else "700-1199" if enriched.offset < 1200
            else "1200-2999" if enriched.offset < 3000 else "3000+"
        ] += 1
        reference[f"{enriched.reference_decision}:{enriched.reference_confidence}"] += 1

    # Highest-confidence benchmark disagreements first, then earliest missed evidence.
    confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "": 3}
    candidates.sort(key=lambda value: (
        0 if value.reference_decision == "KEEP" else 1,
        confidence_order.get(value.reference_confidence, 3), value.offset, value.title,
    ))
    return {
        "database": str(database),
        "run_id": str(run["run_id"]),
        "candidate_version": str(run["candidate_version"]),
        "current_direction_window_chars": CURRENT_DIRECTION_WINDOW,
        "current_lead_window_chars": LEAD_WINDOW,
        "judgment_hold_total": len(rows),
        "judgment_hold_with_fulltext": len(fulltext_rows),
        "direct_company_mention_after_350": direct_after_350,
        "direct_company_mention_after_700": direct_after_700,
        "conservative_missed_direction_items": len(item_candidates),
        "polarity": dict(sorted(polarity.items())),
        "offset_bands": dict(offset_bands),
        "reference_distribution": dict(sorted(reference.items())),
        "samples": [asdict(value) for value in candidates[:80]],
    }


def _markdown(result: dict[str, object]) -> str:
    samples = result["samples"]
    lines = [
        "# 뉴스 본문 방향 판정 범위 전수 감사 — 2026-09-15",
        "",
        "## 범위와 판정 기준",
        "",
        f"- 고정 corpus benchmark 실행: `{result['run_id']}`",
        f"- 후보 버전: `{result['candidate_version']}`",
        f"- 현재 `판단 보류`: {result['judgment_hold_total']:,}건",
        f"- 그중 정제된 원문이 있는 기사: {result['judgment_hold_with_fulltext']:,}건",
        "- 자동 기준 라벨은 사람 정답표가 아니므로 정확도 증명용이 아니라 후보 우선순위용으로만 사용했다.",
        "",
        "## 확인 결과",
        "",
        f"- 현행 코드는 원문 관련성·가격반응 확인에는 앞 {result['current_lead_window_chars']}자를 쓰지만, 방향 단어는 실제로 앞 {result['current_direction_window_chars']}자 안의 대상 회사 문장만 읽는다.",
        f"- 350자 뒤에 대상 회사가 다시 등장한 보류 기사: {result['direct_company_mention_after_350']:,}건",
        f"- 700자 뒤에 대상 회사가 다시 등장한 보류 기사: {result['direct_company_mention_after_700']:,}건",
        f"- 350자 뒤에서 대상 회사 직접 문장, 강한 기업 사건, 비과거 표현, 방향 근거를 모두 만족한 누락 후보: **{result['conservative_missed_direction_items']:,}건**",
        f"- 방향 분포: `{json.dumps(result['polarity'], ensure_ascii=False, sort_keys=True)}`",
        f"- 최초 근거 위치: `{json.dumps(result['offset_bands'], ensure_ascii=False)}`",
        f"- 자동 기준 라벨 분포: `{json.dumps(result['reference_distribution'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 해석",
        "",
        "고정 700자를 단순히 전체 본문으로 늘리면 과거 계약·회사 소개·다른 기업 사건을 다시 끌어와 오분류가 늘어난다. 대신 전체 정제 본문에서 대상 회사가 직접 등장한 문장만 찾고, 과거 표현을 제외한 뒤 강한 기업 사건과 방향 근거가 함께 있는 문장만 보조 근거로 쓰는 방식이 적합하다.",
        "",
        "아래 표는 보수 조건을 모두 통과한 후보 일부다. 문장 전체는 저작권과 가독성을 위해 180자로 줄였다.",
        "",
        "|자동기준|종목|위치|방향|제목|후보 문장|",
        "|---|---|---:|---|---|---|",
    ]
    for sample in samples[:30]:
        title = str(sample["title"]).replace("|", "\\|")
        sentence = str(sample["sentence"])[:180].replace("|", "\\|")
        reference = f"{sample['reference_decision']} {sample['reference_confidence']}".strip()
        lines.append(
            f"|{reference}|{sample['stock_name']}|{sample['offset']}|{sample['polarity']}|{title}|{sentence}|"
        )
    lines.extend([
        "",
        "## 적용 판단",
        "",
        "누락 후보 수와 표본의 실제 오분류 여부를 확인한 뒤에만 제품 규칙을 바꾼다. 적용할 경우 700자 상수를 키우는 방식이 아니라, 대상 회사 중심 문장 선택을 기존 `news_analysis.py` 안에 최소 확장하고 회귀 테스트로 과거 사건·다른 회사 사건·시세 기사 승격을 막는다.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    result = audit(args.database)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(_markdown(result), encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "samples"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
