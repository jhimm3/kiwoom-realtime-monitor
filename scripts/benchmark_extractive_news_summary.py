"""Run the non-AI extractive summary over every body in an offline NAS corpus."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from kiwoom_monitor.application.news_analysis import (
    _MATERIAL_BUSINESS_EVENT,
    _SUBSTANTIVE_EVENT,
    _near_duplicate_tokens,
    _summary_tokens,
    extractive_news_summary,
)
from kiwoom_monitor.infrastructure.article_text import (
    ARTICLE_TEXT_CLEANER_VERSION, clean_article_text,
)


_HARD_NOISE = re.compile(
    r"(?:무단\s*전재|재배포\s*금지|Copyright|All\s+rights\s+reserved|저작권자|"
    r"좋아요\s*\d+\s*나빠요\s*\d+|기자의\s*다른\s*기사|이\s*기자의\s*최신글|"
    r"호가시행일|page\s*\d+|자기주식매매\s*신청내역|기사모음|랭킹\s*뉴스|"
    r"많이\s*본\s*뉴스|함께\s*볼만한\s*뉴스|구독하고\s*메인에서|"
    r"정기간행물\s*등록번호|고충처리인|AI\s*학습\s*및\s*활용\s*금지)",
    re.IGNORECASE,
)
_SOURCE_CREDIT = re.compile(
    r"(?:기자\s*=|\[사진|사진\s*=|자료사진|/공동취재|/뉴스1|/연합뉴스|ⓒ)",
    re.IGNORECASE,
)
_HARD_REASONS = {
    "not_verbatim", "hard_page_noise",
}
_NUMBER = re.compile(r"\d[\d,.]*\s*(%|억원|조원|원|달러|주|건|배|명)")
_TITLE_STOP = {"관련", "대한", "통해", "위해", "기자", "종합", "단독", "속보", "오늘", "이번"}


def _tokens(value: str) -> set[str]:
    return {
        token.casefold() for token in re.findall(r"[0-9A-Za-z가-힣]+", value)
        if len(token) >= 2 and token not in _TITLE_STOP
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=100)
    args = parser.parse_args()

    with sqlite3.connect(args.database) as connection:
        article_rows = connection.execute(
            "SELECT payload_json FROM records WHERE kind='history:article'",
        ).fetchall()
        articles = {}
        for (payload_text,) in article_rows:
            payload = json.loads(payload_text)
            articles[str(payload.get("article_revision_id") or "")] = payload
        names = {
            str(body_revision_id): str(stock_name or "")
            for body_revision_id, stock_name in connection.execute(
                "SELECT body_revision_id, max(stock_name) FROM news_audit_items GROUP BY body_revision_id",
            )
        }
        body_payloads = connection.execute(
            "SELECT payload_json FROM records WHERE kind='history:body' ORDER BY record_id",
        ).fetchall()

        version = hashlib.sha256(
            (inspect.getsource(extractive_news_summary) + ARTICLE_TEXT_CLEANER_VERSION).encode("utf-8"),
        ).hexdigest()[:16]
        run_id = f"extractive-{version}-{int(time.time())}"
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS news_extractive_summary_runs (
                run_id TEXT PRIMARY KEY, version TEXT NOT NULL, created_at REAL NOT NULL,
                total INTEGER NOT NULL, fulltext INTEGER NOT NULL, risky INTEGER NOT NULL,
                report_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS news_extractive_summary_items (
                run_id TEXT NOT NULL, body_revision_id TEXT NOT NULL,
                article_revision_id TEXT NOT NULL, identity TEXT NOT NULL, title TEXT NOT NULL,
                summary_json TEXT NOT NULL, risk_json TEXT NOT NULL,
                title_token_coverage REAL NOT NULL, title_number_coverage REAL NOT NULL,
                PRIMARY KEY (run_id, body_revision_id)
            );
            """,
        )

        reason_counts: Counter[str] = Counter()
        hard_reason_counts: Counter[str] = Counter()
        advisory_counts: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        result_rows = []
        samples: list[dict[str, object]] = []
        fulltext = 0
        for (payload_text,) in body_payloads:
            payload = json.loads(payload_text)
            body_revision_id = str(payload.get("body_revision_id") or "")
            article_revision_id = str(payload.get("article_revision_id") or "")
            status = str(payload.get("status") or "")
            body = clean_article_text(str(payload.get("body_text") or ""))
            if status != "fulltext" or not body:
                category_counts[f"status:{status}"] += 1
                continue
            fulltext += 1
            article = articles.get(str(article_revision_id), {})
            document = article.get("document") if isinstance(article.get("document"), dict) else {}
            title = str(document.get("title") or "")
            identity = str(article.get("identity") or document.get("identity") or "")
            summary = extractive_news_summary(names.get(str(body_revision_id), ""), title, str(body))
            summary_text = " ".join(summary)
            risks: list[str] = []
            if not summary:
                risks.append("abstained")
            if any(sentence not in str(body) for sentence in summary):
                risks.append("not_verbatim")
            if _HARD_NOISE.search(summary_text):
                risks.append("hard_page_noise")
            if _SOURCE_CREDIT.search(summary_text):
                risks.append("source_credit")
            if any(len(sentence) > 900 for sentence in summary):
                risks.append("overlong_sentence")
            if any(
                _near_duplicate_tokens(_summary_tokens(left), _summary_tokens(right))
                for index, left in enumerate(summary) for right in summary[index + 1:]
            ):
                risks.append("near_duplicate")

            body_title_tokens = _tokens(title) & _tokens(str(body))
            summary_title_tokens = body_title_tokens & _tokens(summary_text)
            title_token_coverage = len(summary_title_tokens) / len(body_title_tokens) if body_title_tokens else 1.0
            if len(body_title_tokens) >= 3 and title_token_coverage < 0.15:
                risks.append("low_title_coverage")

            title_number_kinds = set(_NUMBER.findall(title))
            body_number_kinds = set(_NUMBER.findall(str(body)))
            expected_number_kinds = title_number_kinds & body_number_kinds
            summary_number_kinds = set(_NUMBER.findall(summary_text))
            number_coverage = (
                len(expected_number_kinds & summary_number_kinds) / len(expected_number_kinds)
                if expected_number_kinds else 1.0
            )
            if expected_number_kinds and number_coverage < 1.0:
                risks.append("missing_title_number")

            material_lead = str(body)[:900]
            material_in_body = bool(
                _SUBSTANTIVE_EVENT.search(material_lead) or _MATERIAL_BUSINESS_EVENT.search(material_lead)
            )
            material_in_summary = bool(
                _SUBSTANTIVE_EVENT.search(summary_text) or _MATERIAL_BUSINESS_EVENT.search(summary_text)
            )
            if material_in_body and not material_in_summary:
                risks.append("missing_material_event")
            if len(summary_text) > 1800:
                risks.append("summary_too_long")

            risks = list(dict.fromkeys(risks))
            for reason in risks:
                reason_counts[reason] += 1
                (hard_reason_counts if reason in _HARD_REASONS else advisory_counts)[reason] += 1
            hard_reasons = [reason for reason in risks if reason in _HARD_REASONS]
            advisory_reasons = [reason for reason in risks if reason not in _HARD_REASONS]
            category_counts["hard_fail" if hard_reasons else "hard_pass"] += 1
            category_counts["advisory" if advisory_reasons else "no_advisory"] += 1
            category_counts["risky" if risks else "clean"] += 1
            result_rows.append((
                run_id, str(body_revision_id), str(article_revision_id), identity, title,
                json.dumps(summary, ensure_ascii=False), json.dumps(risks, ensure_ascii=False),
                title_token_coverage, number_coverage,
            ))
            if risks and len(samples) < max(0, args.sample_limit):
                samples.append({"title": title, "risks": risks, "summary": summary})

        report_data = {
            "version": version, "cleaner_version": ARTICLE_TEXT_CLEANER_VERSION,
            "total_body_revisions": len(body_payloads), "fulltext": fulltext,
            "clean": category_counts["clean"], "risky": category_counts["risky"],
            "risk_counts": dict(reason_counts),
            "hard_pass": category_counts["hard_pass"],
            "hard_fail": category_counts["hard_fail"],
            "hard_reason_counts": dict(hard_reason_counts),
            "advisory": category_counts["advisory"],
            "advisory_counts": dict(advisory_counts),
        }
        connection.executemany(
            """
            INSERT INTO news_extractive_summary_items (
                run_id, body_revision_id, article_revision_id, identity, title, summary_json,
                risk_json, title_token_coverage, title_number_coverage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            result_rows,
        )
        connection.execute(
            "INSERT INTO news_extractive_summary_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, version, time.time(), len(body_payloads), fulltext, category_counts["risky"],
             json.dumps(report_data, ensure_ascii=False, sort_keys=True)),
        )
        connection.commit()

    lines = [
        "# AI 없는 추출 요약 전수 감사", "",
        f"- run: `{run_id}`", f"- 본문 정제기: `{ARTICLE_TEXT_CLEANER_VERSION}`",
        f"- 전체 body revision: {len(body_payloads):,}건",
        f"- 요약 대상 fulltext: {fulltext:,}건",
        f"- 구조 안전성 통과: {category_counts['hard_pass']:,}건 ({category_counts['hard_pass'] / max(1, fulltext) * 100:.3f}%)",
        f"- 구조상 실패: {category_counts['hard_fail']:,}건 ({category_counts['hard_fail'] / max(1, fulltext) * 100:.3f}%)",
        f"- 의미 검토 신호: {category_counts['advisory']:,}건 ({category_counts['advisory'] / max(1, fulltext) * 100:.3f}%)",
        "", "## 구조상 실패 신호", "",
        *([f"- {reason}: {count:,}건" for reason, count in hard_reason_counts.most_common()] or ["- 없음"]),
        "", "## 의미 검토 신호", "",
        *([f"- {reason}: {count:,}건" for reason, count in advisory_counts.most_common()] or ["- 없음"]),
        "", "## 검토 표본", "",
    ]
    for sample in samples:
        lines.extend([
            f"### {sample['title']}", "",
            f"- 신호: {', '.join(sample['risks'])}",
            *[f"- {sentence}" for sentence in sample["summary"]], "",
        ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"run_id": run_id, **report_data}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
