"""정제된 뉴스 본문 전수 원장에서 과절단과 잔여 페이지 문구를 점검한다."""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import re
from pathlib import Path
from urllib.parse import urlparse


_RESIDUAL_TAIL_MARKERS = (
    "구독하고 메인에서",
    "언론사의 주요 뉴스",
    "주요 뉴스를 메인에서",
    "많이 본 뉴스",
    "랭킹 뉴스",
    "기자 구독",
    "댓글 정책",
    "추천 기사",
    "관련 기사",
    "실시간 인기기사",
    "맨위로 예 아니오",
    "All rights reserved",
)


def build_report(database_path: Path, *, samples: int = 30) -> str:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        articles = _article_metadata(connection)
        rows = connection.execute(
            "SELECT * FROM news_cleaned_bodies ORDER BY body_revision_id"
        ).fetchall()

    marker_counts: collections.Counter[str] = collections.Counter()
    residual_counts: collections.Counter[str] = collections.Counter()
    domain_values: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    aggressive: list[tuple[float, sqlite3.Row]] = []
    residual_samples: list[tuple[str, sqlite3.Row]] = []
    raw_total = clean_total = changed = 0
    fulltext_count = 0
    empty_after_clean = 0
    short_after_clean = 0
    for row in rows:
        raw_length = int(row["raw_body_length"] or 0)
        clean_length = int(row["clean_body_length"] or 0)
        raw_total += raw_length
        clean_total += clean_length
        changed += raw_length != clean_length
        marker = str(row["cut_marker"] or "")
        if marker:
            marker_counts[marker] += 1
        if str(row["status"] or "") == "fulltext":
            fulltext_count += 1
            empty_after_clean += clean_length == 0
            short_after_clean += clean_length < 120
        metadata = articles.get(str(row["article_revision_id"] or ""), {})
        domain = str(metadata.get("domain") or "unknown")
        domain_values[domain].append((raw_length, clean_length))
        if raw_length >= 1_000 and clean_length / raw_length < 0.15:
            aggressive.append((clean_length / raw_length, row))
        without_section_headers = re.sub(
            r"\[관련 기사\s+\d+/\d+\s*:[^]]*\]", "",
            str(row["clean_body_text"] or ""),
        )
        tail = without_section_headers[-1_500:]
        for candidate in _RESIDUAL_TAIL_MARKERS:
            if candidate.casefold() in tail.casefold():
                residual_counts[candidate] += 1
                if len(residual_samples) < samples * 3:
                    residual_samples.append((candidate, row))

    domains = []
    for domain, values in domain_values.items():
        raw = sum(value[0] for value in values)
        clean = sum(value[1] for value in values)
        domains.append((raw - clean, domain, len(values), raw, clean))
    domains.sort(reverse=True)

    lines = [
        "# 뉴스 본문 정제 전수 감사", "",
        f"- 본문 revision: {len(rows):,}",
        f"- fulltext revision: {fulltext_count:,}",
        f"- 정제로 변경된 revision: {changed:,}",
        f"- 원문 문자: {raw_total:,}",
        f"- 정제 문자: {clean_total:,}",
        f"- 제거 문자: {raw_total - clean_total:,} ({(raw_total-clean_total)/raw_total:.2%})" if raw_total else "- 제거 문자: 0",
        f"- 정제 후 빈 fulltext: {empty_after_clean:,}",
        f"- 정제 후 120자 미만 fulltext: {short_after_clean:,}",
        "",
        "## 절단 표식", "",
        "|표식|revision 수|", "|---|---:|",
    ]
    lines.extend(f"|{_cell(marker)}|{count:,}|" for marker, count in marker_counts.most_common())
    lines.extend(["", "## 제거 문자가 많은 도메인", "", "|도메인|revision|원문 문자|제거 문자|제거율|", "|---|---:|---:|---:|---:|"])
    for removed, domain, count, raw, clean in domains[:30]:
        lines.append(f"|{_cell(domain)}|{count:,}|{raw:,}|{removed:,}|{removed/raw:.2%}|" if raw else f"|{_cell(domain)}|{count:,}|0|0|0%|")
    lines.extend(["", "## 과절단 의심 표본", ""])
    for ratio, row in sorted(aggressive, key=lambda value: value[0])[:samples]:
        lines.append(_sample_line(row, articles, f"정제 후 {ratio:.1%}"))
    if not aggressive:
        lines.append("- 없음")
    lines.extend(["", "## 정제 뒤에도 남은 페이지 문구", ""])
    if residual_counts:
        lines.append(", ".join(f"`{value}` {count:,}건" for value, count in residual_counts.most_common()))
        lines.append("")
        seen: set[str] = set()
        for marker, row in residual_samples:
            key = str(row["body_revision_id"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(_sample_line(row, articles, f"잔여 표식: {marker}"))
            if len(seen) >= samples:
                break
    else:
        lines.append("- 검사한 반복 페이지 표식 없음")
    return "\n".join(lines) + "\n"


def _article_metadata(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for (payload_json,) in connection.execute(
        "SELECT payload_json FROM records WHERE kind='history:article'"
    ):
        row = json.loads(str(payload_json))
        document = row.get("document") or {}
        link = str(document.get("original_link") or document.get("link") or "")
        result[str(row.get("article_revision_id") or "")] = {
            "title": " ".join(str(document.get("title") or "").split()),
            "domain": urlparse(link).netloc or "unknown",
        }
    return result


def _sample_line(row: sqlite3.Row, articles: dict[str, dict[str, str]], reason: str) -> str:
    metadata = articles.get(str(row["article_revision_id"] or ""), {})
    return (
        f"- **{_single(metadata.get('domain') or 'unknown')}** "
        f"{_single(metadata.get('title') or '(제목 없음)')} — {reason}; "
        f"{int(row['raw_body_length'] or 0):,}자 → {int(row['clean_body_length'] or 0):,}자"
    )


def _cell(value: object) -> str:
    return _single(value).replace("|", "\\|")


def _single(value: object) -> str:
    return " ".join(str(value or "").split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    arguments = parser.parse_args()
    report = build_report(arguments.database, samples=arguments.samples)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(report, encoding="utf-8")
    print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
