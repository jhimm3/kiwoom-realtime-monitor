"""Apply the candidate query-set storage gate to an exported news corpus."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from kiwoom_monitor.central_server.news_sources import _storage_skip_reason


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sample-limit", type=int, default=100)
    args = parser.parse_args()

    with sqlite3.connect(args.database) as connection:
        rows = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload_json FROM records WHERE kind='history:article'",
            )
        ]

    target_identities = {
        str(row.get("identity") or "") for row in rows
        if str(row.get("stock_code") or "") not in {"", "GLOBAL"}
    }
    latest: dict[str, dict] = {}
    for row in rows:
        if str(row.get("stock_code") or "") != "GLOBAL":
            continue
        if str(row.get("collection_scope") or "") != "query_set":
            continue
        identity = str(row.get("identity") or "")
        if not identity:
            continue
        previous = latest.get(identity)
        if previous is None or float(row.get("received_at") or 0) >= float(previous.get("received_at") or 0):
            latest[identity] = row

    counts = {"kept_target": 0, "kept_signal": 0, "title_only": 0, "irrelevant": 0}
    samples: dict[str, list[str]] = {"title_only": [], "irrelevant": []}
    for identity, row in latest.items():
        document = row.get("document") if isinstance(row.get("document"), dict) else {}
        item = SimpleNamespace(
            title=str(document.get("title") or ""),
            description=str(document.get("description") or ""),
        )
        targeted = identity in target_identities
        targets = [{"relation_status": "confirmed"}] if targeted else [{"relation_status": "unresolved"}]
        reason = _storage_skip_reason(item, targets)  # type: ignore[arg-type]
        if reason:
            counts[reason] += 1
            if len(samples[reason]) < max(0, args.sample_limit):
                samples[reason].append(item.title)
        else:
            counts["kept_target" if targeted else "kept_signal"] += 1

    total = len(latest)
    skipped = counts["title_only"] + counts["irrelevant"]
    lines = [
        "# 뉴스 신규 저장 필터 오프라인 감사",
        "",
        f"- 평가한 query-set 최신 기사: {total:,}건",
        f"- 저장 유지: {total - skipped:,}건 ({(total - skipped) / max(1, total) * 100:.3f}%)",
        f"- 종목 연결로 유지: {counts['kept_target']:,}건",
        f"- 시장·기업사건 문맥으로 유지: {counts['kept_signal']:,}건",
        f"- 제목뿐이라 제외: {counts['title_only']:,}건",
        f"- 명백한 무관 후보: {counts['irrelevant']:,}건",
        "",
        "## 무관 후보 표본",
        "",
        *[f"- {title}" for title in samples["irrelevant"]],
        "",
        "## 제목뿐인 후보 표본",
        "",
        *[f"- {title}" for title in samples["title_only"]],
        "",
    ]
    report = "\n".join(lines)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
