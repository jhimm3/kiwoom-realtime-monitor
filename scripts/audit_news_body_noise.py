"""로컬 뉴스 corpus의 본문 길이와 반복 꼬리 문구를 점검한다."""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_TAIL_MARKERS = (
    "Copyright ⓒ",
    "무단 전재 및 재배포 금지",
    "이 기사는 언론사에서",
    "기자 프로필",
    "언론사 주요뉴스",
    "이 기사를 추천합니다",
    "많이 본 뉴스",
    "함께 볼만한 뉴스",
)


def audit(database_path: Path, *, top: int = 30) -> dict[str, Any]:
    lengths: list[int] = []
    line_counts: collections.Counter[str] = collections.Counter()
    domain_lengths: dict[str, list[int]] = collections.defaultdict(list)
    marker_counts: collections.Counter[str] = collections.Counter()
    potential_trimmed_chars = 0
    with sqlite3.connect(database_path) as connection:
        revision_domains: dict[str, str] = {}
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM records WHERE kind='history:article'"
        ):
            article = json.loads(str(payload_json))
            document = article.get("document") or {}
            link = str(document.get("original_link") or document.get("link") or "")
            revision_domains[str(article.get("article_revision_id") or "")] = urlparse(link).netloc or "unknown"
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM records WHERE kind='history:body'"
        ):
            row = json.loads(str(payload_json))
            if row.get("status") != "fulltext":
                continue
            body = str(row.get("body_text") or "")
            length = len(body)
            lengths.append(length)
            domain = revision_domains.get(str(row.get("article_revision_id") or ""), "unknown")
            domain_lengths[domain].append(length)
            earliest = len(body)
            for marker in _TAIL_MARKERS:
                position = body.find(marker)
                if position >= 0:
                    marker_counts[marker] += 1
                    earliest = min(earliest, position)
            potential_trimmed_chars += len(body) - earliest
            for line in _lines(body):
                if 20 <= len(line) <= 240:
                    line_counts[line] += 1

    ordered = sorted(lengths)
    domains = sorted(
        (
            {
                "domain": domain,
                "count": len(values),
                "median_length": int(statistics.median(values)),
                "p90_length": _percentile(values, 0.9),
            }
            for domain, values in domain_lengths.items()
        ),
        key=lambda value: (-value["median_length"], -value["count"]),
    )
    repeated = [
        {"count": count, "text": text}
        for text, count in line_counts.most_common(top)
        if count >= 5
    ]
    return {
        "fulltext_count": len(lengths),
        "length": {
            "minimum": min(ordered) if ordered else 0,
            "median": int(statistics.median(ordered)) if ordered else 0,
            "p90": _percentile(ordered, 0.9),
            "p99": _percentile(ordered, 0.99),
            "maximum": max(ordered) if ordered else 0,
            "over_10000": sum(value > 10_000 for value in ordered),
            "over_30000": sum(value > 30_000 for value in ordered),
        },
        "domains_by_median_length": domains[:top],
        "tail_markers": dict(marker_counts.most_common()),
        "potential_trimmed_chars": potential_trimmed_chars,
        "repeated_lines": repeated,
    }


def _lines(body: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", value).strip()
        for value in re.split(r"[\r\n]+", body)
        if value.strip()
    ]


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * ratio))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--top", type=int, default=30)
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.database, top=arguments.top), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
